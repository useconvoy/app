"use client";

import { useState } from "react";
import { useWorkspace } from "@/lib/configurations/client";
import { fmtCount, runLabel } from "@/lib/configurations/format";
import { queueEvaluation } from "@/lib/configurations/mutations";
import type { EvalRun, EvalSuite, Robot } from "@/lib/configurations/types";
import { FactList } from "../../Facts";
import { Notice } from "../../Notice";
import { Modal } from "../../Overlay";

/**
 * Confirms a re-run and queues it with `queueEvaluation` through `save()`. The new
 * run is only a queued record: no evaluation runner is connected, and the
 * dialog says so before and after.
 */
export function RerunDialog({ open, onClose, onQueued, run, suite, robot, configName }: {
  open: boolean; onClose: () => void; onQueued: (queued: EvalRun) => void; run: EvalRun; suite: EvalSuite; robot: Robot; configName: string;
}) {
  const ws = useWorkspace();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const purpose = `Re-run of ${runLabel(run)}${run.purpose ? ` · ${run.purpose}` : ""}`;
  async function confirm() {
    setBusy(true); setError(null);
    let queued: EvalRun | null = null;
    try {
      const result = await ws.save(current => {
        const next = queueEvaluation(current, { configId: run.configId, rev: run.rev, robotId: run.robotId, suiteId: suite.id, variant: run.variant, purpose }, Date.now());
        queued = next.run;
        return next.workspace;
      });
      if (!result.ok) { setError(result.error); return; }
      const saved = queued as EvalRun | null;
      if (saved) onQueued(result.workspace.runs.find(item => item.id === saved.id) ?? saved);
    } finally { setBusy(false); }
  }
  function dismiss() { if (!busy) { setError(null); onClose(); } }
  return <Modal open={open} onClose={dismiss} eyebrow={`Re-run · ${runLabel(run)}`} title="Queue this evaluation again?"
    footer={<>
      <button className="btn btn-secondary cfg-btn" type="button" onClick={dismiss} disabled={busy}>Cancel</button>
      <button className="btn btn-primary cfg-btn" type="button" onClick={() => void confirm()} disabled={busy || !ws.canSave}>{busy ? "Queueing…" : "Queue run"}</button>
    </>}>
    <FactList facts={[
      { label: "Suite", value: `${suite.name} ${suite.version}`, detail: `${fmtCount(suite.episodesPerRun)} episodes · ${suite.simEngine}` },
      { label: "Configuration", value: `${configName} ${run.rev}` },
      { label: "Variant", value: run.variant },
      { label: "Robot", value: robot.name },
      { label: "Purpose", value: purpose },
    ]} />
    <Notice tone="info">No evaluation runner is connected to this workspace. The new run is stored as <strong>queued</strong> with no results, and it stays queued until a runner reports them.</Notice>
    {ws.source === "sample" && ws.canSave && <p className="portal-context-note">This account has no workspace document yet. Queueing saves the sample workspace, with the new run, as this account’s document.</p>}
    {!ws.canSave && <Notice tone="warning" icon="blocked">The stored workspace could not be read, so changes are not saved. Reload, or import a valid workspace, to queue runs.</Notice>}
    {error && <Notice tone="error">{error}</Notice>}
  </Modal>;
}
