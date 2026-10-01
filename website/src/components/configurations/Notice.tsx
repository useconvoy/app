"use client";

import { useState } from "react";
import type { ChangeEvent, ReactNode } from "react";
import { useWorkspace } from "@/lib/configurations/client";
import type { LiveBinding } from "@/lib/configurations/live";
import { currentRevision, getConfiguration } from "@/lib/configurations/selectors";
import type { ConvoyWorkspace } from "@/lib/configurations/types";
import { summarizeIssues } from "@/lib/configurations/validate";
import { Icon, type IconName } from "./Icons";

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
 * The provenance notice that opens every Configurations page: "<label>. Values marked
 * Sample are illustrative …" plus one sentence per live-bound robot. Pass `configId`
 * to name only that configuration's bound robots, and `live` (from `useLiveRobots`)
 * when the page has it, so an unreachable device is not described as reporting.
 */
export function WorkspaceNotice({ workspace, configId, live }: { workspace: ConvoyWorkspace; configId?: string; live?: Readonly<Record<string, LiveBinding | undefined>> }) {
  const bound = workspace.robots.filter(robot => robot.deviceId && robot.configId && (!configId || robot.configId === configId));
  return <Notice tone="info">
    {workspace.meta.label}. Values marked <strong>Sample</strong> are illustrative and prepared for discussion; they are not measurements.
    {bound.map(robot => {
      const config = robot.configId ? getConfiguration(workspace, robot.configId) : null;
      const hardware = config ? currentRevision(config).edgeHardware.name : "edge device";
      const unavailable = live?.[robot.id]?.status === "unavailable";
      return <span key={robot.id}> <strong>{robot.name}</strong> {unavailable ? `is bound to a connected ${hardware}; no device report is available right now.` : `reports from a connected ${hardware}.`}</span>;
    })}
  </Notice>;
}

/**
 * Explains where the workspace came from when it is not the stored document: no
 * document yet (with "Import workspace"), an invalid document (with the first
 * problems and "Import workspace"), an unreadable one (with "Try again"), or a
 * failed save. Renders nothing when the stored document is shown without errors.
 */
export function WorkspaceSourceNotice() {
  const ws = useWorkspace();
  if (ws.status !== "ready") return null;
  if (ws.source === "sample" && ws.reason === "missing") {
    return <Notice tone="info" action={<ImportWorkspaceButton />}>No workspace document is stored for this account yet, so the <strong>sample workspace</strong> is shown. Import a workspace file to use your own.</Notice>;
  }
  if (ws.source === "sample" && ws.reason === "invalid") {
    return <Notice tone="warning" icon="blocked" action={<ImportWorkspaceButton />}>
      The stored workspace could not be used, so the <strong>sample workspace</strong> is shown and changes are not saved.
      <span className="cfg-issues">{ws.issues.slice(0, 3).map(issue => <span key={`${issue.path}:${issue.message}`}>{issue.path ? `${issue.path}: ` : ""}{issue.message}</span>)}{ws.issues.length > 3 && <span>and {ws.issues.length - 3} more</span>}</span>
    </Notice>;
  }
  if (ws.source === "sample" && ws.reason === "unavailable") {
    return <Notice tone="warning" action={<button className="btn btn-secondary cfg-btn" type="button" onClick={() => void ws.refresh()}>Try again</button>}>{ws.error ?? "The workspace document could not be read."} The <strong>sample workspace</strong> is shown and changes are not saved.</Notice>;
  }
  if (ws.error) return <Notice tone="error">{ws.error}</Notice>;
  return null;
}

/** File picker that validates a workspace JSON file and stores it as the account's document. */
export function ImportWorkspaceButton({ label = "Import workspace" }: { label?: string }) {
  const ws = useWorkspace();
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  async function choose(event: ChangeEvent<HTMLInputElement>) {
    const input = event.currentTarget;
    const file = input.files?.[0];
    input.value = "";
    if (!file) return;
    setBusy(true); setProblem(null);
    try {
      let parsed: unknown;
      try { parsed = JSON.parse(await file.text()); }
      catch { setProblem("This file is not JSON."); return; }
      const result = await ws.importDocument(parsed);
      if (!result.ok) setProblem(result.issues?.length ? `Not imported: ${summarizeIssues(result.issues)}` : result.error);
    } finally { setBusy(false); }
  }
  return <span className="cfg-import">
    <label className="btn btn-secondary cfg-btn cfg-file">{busy ? "Importing…" : label}<input type="file" accept="application/json,.json" disabled={busy} onChange={event => void choose(event)} /></label>
    {problem && <span role="alert" className="cfg-import__error">{problem}</span>}
  </span>;
}
