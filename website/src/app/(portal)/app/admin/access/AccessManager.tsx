/**
 * The early-access list manager: add an email or domain entry, see who is
 * on the list, remove entries. Results render inline; the server actions
 * re-check the admin gate and write the audit rows.
 */
"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/Button";
import type { AccessActionResult } from "@/lib/orgs/access-actions";

export interface AllowlistEntry {
  entry: string;
  note: string | null;
  added: string;
}

export interface AccessManagerProps {
  entries: AllowlistEntry[];
  add: (entry: string, note: string) => Promise<AccessActionResult>;
  remove: (entry: string) => Promise<AccessActionResult>;
}

export function AccessManager({ entries, add, remove }: AccessManagerProps) {
  const router = useRouter();
  const [entry, setEntry] = useState("");
  const [note, setNote] = useState("");
  const [result, setResult] = useState<AccessActionResult | null>(null);
  const [pending, startTransition] = useTransition();

  function submit() {
    startTransition(async () => {
      const outcome = await add(entry, note);
      setResult(outcome);
      if (outcome.ok) {
        setEntry("");
        setNote("");
        router.refresh();
      }
    });
  }

  function removeEntry(value: string) {
    startTransition(async () => {
      const outcome = await remove(value);
      setResult(outcome);
      router.refresh();
    });
  }

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-line bg-card p-4 space-y-2">
        <label htmlFor="access-entry" className="block text-sm font-medium text-ink">
          Email or domain
        </label>
        <input
          id="access-entry"
          type="text"
          value={entry}
          onChange={(event) => setEntry(event.target.value)}
          placeholder="person@company.com or @company.com"
          className="w-full rounded-md border border-line bg-card px-2 py-1.5 font-mono text-sm text-ink"
        />
        <label htmlFor="access-note" className="block text-sm font-medium text-ink">
          Note
        </label>
        <input
          id="access-note"
          type="text"
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder="Who this is, so the list stays legible"
          className="w-full rounded-md border border-line bg-card px-2 py-1.5 text-sm text-ink"
        />
        <div className="flex items-center gap-3">
          <Button onClick={submit} pending={pending}>
            Allow sign-in
          </Button>
          {result && (
            <p className={`text-xs ${result.ok ? "text-pass-text" : "text-fail"}`} role="status">
              {result.message}
            </p>
          )}
        </div>
      </div>
      {entries.length > 0 ? (
        <ul className="m-0 list-none space-y-2 p-0">
          {entries.map((row) => (
            <li
              key={row.entry}
              className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line bg-card p-4"
            >
              <div>
                <p className="font-mono text-sm text-ink">{row.entry}</p>
                <p className="mt-0.5 text-xs text-muted">
                  {row.note ? `${row.note} · ` : ""}added {row.added}
                </p>
              </div>
              <Button variant="secondary" onClick={() => removeEntry(row.entry)}>
                Remove
              </Button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="rounded-lg border border-line bg-card p-4 text-sm text-muted">
          Nobody is on the list yet. People with existing accounts can still sign in.
        </p>
      )}
    </div>
  );
}
