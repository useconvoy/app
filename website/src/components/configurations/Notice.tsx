"use client";

import { useState } from "react";
import type { ChangeEvent, ReactNode } from "react";
import { IMPORT_MAX_BYTES, useWorkspace } from "@/lib/configurations/client";
import { summarizeIssues } from "@/lib/configurations/validate";
import { Icon } from "./Icons";
import { Modal } from "./Overlay";

export type NoticeTone = "info" | "warning" | "error";

/** One short line, only when something needs doing. `action` sits at the end. */
export function Notice({ tone = "info", children, action }: { tone?: NoticeTone; children: ReactNode; action?: ReactNode }) {
  return <div className={`cv-notice cv-notice--${tone}`} role={tone === "error" ? "alert" : "status"}>
    <p>{children}</p>
    {action && <span className="cv-notice__action">{action}</span>}
  </div>;
}

/**
 * The workspace's state when it needs attention: the stored workspace could not be
 * read (or is not valid) so the sample is shown, or the last save failed.
 */
export function WorkspaceNotice() {
  const ws = useWorkspace();
  if (ws.status !== "ready") return null;
  if (ws.source === "sample" && ws.reason === "invalid") {
    return <Notice tone="warning" action={<ImportButton />}><span title={summarizeIssues(ws.issues)}>Saved workspace is not valid. Showing sample.</span></Notice>;
  }
  if (ws.source === "sample" && ws.reason === "unavailable") {
    return <Notice tone="warning" action={<button className="cv-link" type="button" onClick={() => void ws.refresh()}>Retry</button>}>Workspace unavailable. Showing sample.</Notice>;
  }
  return ws.error ? <Notice tone="error">{ws.error}</Notice> : null;
}

/**
 * Imports a workspace JSON file as the account's document. Files over the 2 MiB
 * document limit are refused before they are read. With no document stored it
 * creates one; when one is stored (even an invalid one) it asks first, then
 * replaces exactly the revision that was read.
 */
export function ImportButton() {
  const ws = useWorkspace();
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<{ name: string; value: unknown } | null>(null);
  async function store(value: unknown, replace: boolean) {
    setBusy(true); setProblem(null);
    try {
      const result = await ws.importDocument(value, { replace });
      if (!result.ok) setProblem(result.issues?.length ? `Not imported: ${summarizeIssues(result.issues)}` : result.error);
    } finally { setBusy(false); }
  }
  async function choose(event: ChangeEvent<HTMLInputElement>) {
    const input = event.currentTarget;
    const file = input.files?.[0];
    input.value = "";
    if (!file) return;
    setProblem(null);
    if (file.size > IMPORT_MAX_BYTES) { setProblem(`Not imported: ${(file.size / 1024 / 1024).toFixed(1)} MiB; the limit is 2 MiB.`); return; }
    let parsed: unknown;
    try { parsed = JSON.parse(await file.text()); }
    catch { setProblem("Not imported: not JSON."); return; }
    if (ws.documentExists === true) setConfirm({ name: file.name, value: parsed });
    else await store(parsed, false);
  }
  return <span className="cv-import">
    <label className="cv-btn cv-btn--secondary cv-file"><Icon name="upload" />{busy ? "Importing…" : "Import"}<input type="file" accept="application/json,.json" aria-label="Import workspace" disabled={busy} onChange={event => void choose(event)} /></label>
    {problem && <span role="alert" className="cv-import__error">{problem}</span>}
    <Modal open={confirm !== null} onClose={() => setConfirm(null)} title="Replace the saved workspace?"
      footer={<>
        <button className="cv-btn cv-btn--secondary" type="button" onClick={() => setConfirm(null)} data-autofocus="">Cancel</button>
        <button className="cv-btn cv-btn--primary" type="button" onClick={() => { const chosen = confirm; setConfirm(null); if (chosen) void store(chosen.value, true); }}>Replace</button>
      </>}>
      <p>{confirm?.name} replaces this account&apos;s workspace.</p>
    </Modal>
  </span>;
}
