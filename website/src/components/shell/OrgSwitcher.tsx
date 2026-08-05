"use client";

import { useRouter } from "next/navigation";
import { useRef, useState, useTransition } from "react";
import { switchOrganization } from "@/lib/orgs/actions";

export interface OrgOption {
  id: string;
  name: string;
}

/**
 * Top-bar organization switcher. The list comes from the server layout;
 * choosing an organization calls the server action (which rewrites the
 * session's active org) and then refreshes the tree.
 */
export function OrgSwitcher({ orgs, currentOrgId }: { orgs: OrgOption[]; currentOrgId: string }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [isPending, startTransition] = useTransition();
  const buttonRef = useRef<HTMLButtonElement>(null);
  const current = orgs.find((org) => org.id === currentOrgId);

  function choose(orgId: string) {
    setOpen(false);
    buttonRef.current?.focus();
    if (orgId === currentOrgId) return;
    startTransition(async () => {
      await switchOrganization(orgId);
      router.refresh();
    });
  }

  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key === "Escape" && open) {
      event.preventDefault();
      setOpen(false);
      buttonRef.current?.focus();
    }
  }

  return (
    <div className="relative" onKeyDown={onKeyDown}>
      <button
        ref={buttonRef}
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        disabled={isPending}
        onClick={() => setOpen((value) => !value)}
        className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm font-medium text-ink transition-colors hover:bg-field disabled:opacity-60"
      >
        <span className="flex h-6 w-6 items-center justify-center rounded bg-pine font-display text-xs text-card">
          {(current?.name ?? "Organization").slice(0, 1).toUpperCase()}
        </span>
        {current?.name ?? "Organization"}
        <svg aria-hidden="true" viewBox="0 0 12 12" fill="none" className="h-3 w-3 text-muted">
          <path d="M3 4.5 6 7.5 9 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {open ? (
        <div
          role="listbox"
          aria-label="Switch organization"
          className="absolute left-0 top-10 z-40 w-64 rounded-lg border border-line bg-card p-1 shadow-lg"
        >
          {orgs.map((org) => (
            <button
              key={org.id}
              type="button"
              role="option"
              aria-selected={org.id === currentOrgId}
              onClick={() => choose(org.id)}
              className={`flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-sm transition-colors hover:bg-field ${
                org.id === currentOrgId ? "text-ink" : "text-muted"
              }`}
            >
              {org.name}
              {org.id === currentOrgId ? (
                <svg aria-hidden="true" viewBox="0 0 12 12" fill="none" className="h-3 w-3 text-pine">
                  <path d="M2.5 6.5 5 9l4.5-6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              ) : null}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
