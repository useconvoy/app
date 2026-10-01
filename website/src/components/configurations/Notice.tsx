"use client";

import { useMemo, useState } from "react";
import type { ChangeEvent, ReactNode } from "react";
import { IMPORT_MAX_BYTES, useWorkspace } from "@/lib/configurations/client";
import { fmtDateTime } from "@/lib/configurations/format";
import { deviceSentence } from "@/lib/configurations/live";
import { currentRevision, getConfiguration } from "@/lib/configurations/selectors";
import type { ConvoyWorkspace } from "@/lib/configurations/types";
import { summarizeIssues } from "@/lib/configurations/validate";
import { Icon, type IconName } from "./Icons";
import { useLiveRobots } from "./LiveDeviceProvider";
import { Modal } from "./Overlay";

export type NoticeTone = "info" | "warning" | "success" | "error";
const NOTICE_CLASS: Record<NoticeTone, string> = { info: "console-notice cfg-notice", warning: "portal-notice cfg-notice", success: "cfg-notice cfg-notice--success", error: "portal-notice portal-notice-error cfg-notice" };
const NOTICE_ICON: Record<NoticeTone, IconName> = { info: "info", warning: "warning", success: "check", error: "warning" };

/**
 * Info (blue, role note), warning (amber, role status; use icon "flag" for the attention banner
 * and "blocked" for a blocked state), success (only after a real backend result) or error (role alert).
 * `action` sits at the end of the row (a button or link).
 */
export function Notice({ tone = "info", icon, children, action }: { tone?: NoticeTone; icon?: IconName; children: ReactNode; action?: ReactNode }) {
  return <div className={`${NOTICE_CLASS[tone]}${action ? " cfg-notice--action" : ""}`} role={tone === "info" ? "note" : tone === "error" ? "alert" : "status"}>
    <Icon name={icon ?? NOTICE_ICON[tone]} />
    <p>{children}</p>
    {action && <div className="cfg-notice__action">{action}</div>}
  </div>;
}

/**
 * The one notice that opens every Configurations page. It says where the workspace
 * comes from — the stored document, or the sample when there is no document yet
 * (with Import workspace), when the stored one is invalid (its first problems and
 * Import workspace) or cannot be read (Try again) — what values marked Sample
 * mean, and for each live-bound robot (only `configId`'s when given) whether its
 * device reports right now. Device status comes from the shared live poller
 * (`useLiveRobots`), so a page never claims a report it has not received. A failed
 * save on a stored document adds an error notice.
 */
export function WorkspaceNotice({ workspace, configId }: { workspace: ConvoyWorkspace; configId?: string }) {
  const ws = useWorkspace();
  const bound = useMemo(() => workspace.robots.filter(robot => robot.deviceId && robot.configId && (!configId || robot.configId === configId)), [workspace, configId]);
  const live = useLiveRobots(bound);
  const evidence = <>Values marked <strong>Sample</strong> are illustrative and prepared for discussion; they are not measurements.
    {bound.map(robot => {
      const config = robot.configId ? getConfiguration(workspace, robot.configId) : null;
      const hardware = config ? currentRevision(config).edgeHardware.name : "edge device";
      return <span key={robot.id}> <strong>{robot.name}</strong> {deviceSentence(live[robot.id], hardware)}</span>;
    })}</>;
  if (ws.status === "ready" && ws.source === "sample") {
    if (ws.reason === "missing") {
      return <Notice tone="info" action={<ImportWorkspaceButton />}><strong>{workspace.meta.label}</strong>: no workspace document is stored for this account yet. {evidence}</Notice>;
    }
    if (ws.reason === "invalid") {
      return <Notice tone="warning" icon="blocked" action={<ImportWorkspaceButton />}>
        The stored workspace could not be used, so the <strong>sample workspace</strong> is shown and changes are not saved.
        <span className="cfg-issues">{ws.issues.slice(0, 3).map(issue => <span key={`${issue.path}:${issue.message}`}>{issue.path ? `${issue.path}: ` : ""}{issue.message}</span>)}{ws.issues.length > 3 && <span>and {ws.issues.length - 3} more</span>}</span>
        {evidence}
      </Notice>;
    }
    return <Notice tone="warning" action={<button className="btn btn-secondary cfg-btn" type="button" onClick={() => void ws.refresh()}>Try again</button>}>
      {ws.error ?? "The workspace document could not be read."} The <strong>sample workspace</strong> is shown and changes are not saved. {evidence}
    </Notice>;
  }
  return <>
    <Notice tone="info">{workspace.meta.label}. {evidence}</Notice>
    {ws.status === "ready" && ws.error && <Notice tone="error">{ws.error}</Notice>}
  </>;
}

/**
 * File picker that validates a workspace JSON file and stores it as the account's
 * document. Files over the 2 MiB document limit are refused before they are read.
 * With no document stored it creates one; when one is stored (even an invalid
 * one) it asks first, then replaces exactly the revision that was read.
 */
export function ImportWorkspaceButton({ label = "Import workspace" }: { label?: string }) {
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
    if (file.size > IMPORT_MAX_BYTES) { setProblem(`This file is ${(file.size / 1024 / 1024).toFixed(1)} MiB; a workspace document holds at most 2 MiB.`); return; }
    let parsed: unknown;
    try { parsed = JSON.parse(await file.text()); }
    catch { setProblem("This file is not JSON."); return; }
    if (ws.documentExists === true) setConfirm({ name: file.name, value: parsed });
    else await store(parsed, false);
  }
  const updated = ws.documentUpdatedAt ? ` updated ${fmtDateTime(ws.documentUpdatedAt)}` : "";
  return <span className="cfg-import">
    <label className="btn btn-secondary cfg-btn cfg-file">{busy ? "Importing…" : label}<input type="file" accept="application/json,.json" disabled={busy} onChange={event => void choose(event)} /></label>
    {problem && <span role="alert" className="cfg-import__error">{problem}</span>}
    <Modal open={confirm !== null} onClose={() => setConfirm(null)} eyebrow="Import workspace" title="Replace the stored workspace?"
      footer={<>
        <button className="btn btn-secondary cfg-btn" type="button" onClick={() => setConfirm(null)} data-autofocus="">Cancel</button>
        <button className="btn btn-primary cfg-btn" type="button" onClick={() => { const chosen = confirm; setConfirm(null); if (chosen) void store(chosen.value, true); }}>Replace workspace</button>
      </>}>
      <p>This account already stores a workspace document{ws.documentRevision !== null ? ` (revision ${ws.documentRevision}${updated})` : updated}. Importing <strong>{confirm?.name}</strong> replaces it for this account; the stored document is not kept. If it changed since this page read it, nothing is replaced.</p>
    </Modal>
  </span>;
}
