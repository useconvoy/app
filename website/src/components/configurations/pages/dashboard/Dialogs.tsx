"use client";

import Link from "next/link";
import { useId, useState } from "react";
import type { FormEvent } from "react";
import { currentRevision, getRobot, getSuite, latestGateRun, robotsFor, runHref, runsFor } from "@/lib/configurations/client";
import { revisionGate } from "@/lib/configurations/dashboard";
import type { RobotRow } from "@/lib/configurations/dashboard";
import { fmtCount, fmtDateTime, runLabel } from "@/lib/configurations/format";
import { clearFlag, flagRobot, promoteRevision, queueEvaluation } from "@/lib/configurations/mutations";
import type { Configuration, ConvoyWorkspace, EvalRun, FlagSeverity } from "@/lib/configurations/types";
import { Badge, ProvenanceBadge, RunStatus } from "../../Badges";
import { Fact } from "../../Facts";
import { Notice } from "../../Notice";
import { Modal } from "../../Overlay";
import { GateSummary } from "./AddRobotDialog";
import type { Save } from "./AddRobotDialog";
import { revisionStatus } from "./Specification";

const ROUTE_MODE = { hybrid: "Hybrid", "cloud-only": "Cloud only", "edge-only": "Edge only" } as const;
const UNSAVABLE = "Changes cannot be saved while the stored workspace is unreadable.";

/* ---------- run eval suite ---------- */

/**
 * The configuration's runs in progress and latest gate decision (with links), and
 * a form that queues a suite run on a test robot. A queued run is a record in the
 * workspace; the dialog says plainly that no runner is connected to execute it.
 */
export function RunEvalDialog({ workspace, config, rows, canSave, save, onClose, onDone }: {
  workspace: ConvoyWorkspace; config: Configuration; rows: readonly RobotRow[]; canSave: boolean; save: Save;
  onClose: () => void; onDone: (run: EvalRun | null) => void;
}) {
  const suite = getSuite(workspace, config.suiteId);
  const [robots] = useState(() => rows.filter(row => row.robot.role === "test").map(row => row.robot));
  const revisions = config.revisions.toReversed();
  const [rev, setRev] = useState(config.candidateRev ?? config.productionRev ?? revisions[0]?.rev ?? "");
  const [robotId, setRobotId] = useState(robots[0]?.id ?? "");
  const [variant, setVariant] = useState<string>(ROUTE_MODE[currentRevision(config).routing.mode]);
  const [purpose, setPurpose] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const id = useId();
  const formId = `${id}-form`, noteId = `${id}-note`;
  const runs = runsFor(workspace, { configId: config.id });
  const active = runs.filter(run => run.status === "running" || run.status === "queued");
  const gateRun = latestGateRun(workspace, config.id);
  const reason = !canSave ? UNSAVABLE : !suite ? "No evaluation suite is set for this configuration."
    : !robots.length ? "Attach a test robot to run the suite on." : !variant.trim() ? "Name the policy variant." : null;
  const disabled = busy || reason !== null;
  const runLink = (run: EvalRun) => { const href = runHref(workspace, run); return href ? <Link className="portal-table-link" href={href}>{runLabel(run)}</Link> : runLabel(run); };

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (disabled || !suite) return;
    setBusy(true);
    setError(null);
    const input = { configId: config.id, rev, robotId, suiteId: suite.id, variant: variant.trim(), ...(purpose.trim() ? { purpose: purpose.trim() } : {}) };
    const result = await save(current => queueEvaluation(current, input, Date.now()).workspace);
    setBusy(false);
    if (!result.ok) { setError(result.error); return; }
    const queued = result.workspace.runs.filter(run => run.status === "queued" && run.configId === config.id && run.robotId === robotId && run.rev === rev).toSorted((a, b) => b.number - a.number)[0] ?? null;
    onDone(queued);
  }

  return <Modal open onClose={onClose} eyebrow={config.name} title="Run eval suite"
    footer={<>
      {reason && <span className="cd-foot-note" id={noteId}>{reason}</span>}
      <button className="btn btn-secondary cfg-btn" type="button" onClick={onClose}>Cancel</button>
      <button className="btn btn-primary cfg-btn" type="submit" form={formId} aria-disabled={disabled || undefined} aria-describedby={reason ? noteId : undefined}>{busy ? "Queueing…" : "Queue run"}</button>
    </>}>
    <dl className="portal-facts cd-facts">
      <Fact label="Suite" value={suite ? `${suite.name} ${suite.version}` : "Not set"} detail={suite ? `${fmtCount(suite.episodesPerRun)} episodes · ${suite.simEngine} · ${suite.description}` : undefined} />
      <Fact label="In progress" value={active.length ? <span className="cd-runs">{active.map(run => <span className="cfg-inline" key={run.id}>{runLink(run)}<RunStatus run={run} /><span>{run.rev} on {getRobot(workspace, run.robotId)?.name ?? run.robotId}</span></span>)}</span> : "No run is queued or running"} />
      <Fact label="Latest gate decision" value={gateRun ? <span className="cfg-inline">{runLink(gateRun)}<RunStatus run={gateRun} /><span>{gateRun.rev}</span><ProvenanceBadge provenance={gateRun.provenance} /></span> : "No gated suite run yet"} />
    </dl>
    <form id={formId} className="cd-form" onSubmit={event => void submit(event)} noValidate>
      <div className="cd-row">
        <label className="cfg-field">Revision
          <select className="field-input cfg-input" value={rev} disabled={busy} onChange={event => setRev(event.target.value)} data-autofocus="">
            {revisions.map(item => { const status = revisionStatus(config, item.rev); return <option key={item.rev} value={item.rev}>{item.rev}{status ? ` · ${status}` : ""}</option>; })}
          </select>
        </label>
        <label className="cfg-field">Test robot
          <select className="field-input cfg-input" value={robotId} disabled={busy || !robots.length} onChange={event => setRobotId(event.target.value)}>
            {robots.length ? robots.map(robot => <option key={robot.id} value={robot.id}>{robot.name} · {robot.site}</option>) : <option value="">No test robot attached</option>}
          </select>
        </label>
        <label className="cfg-field">Policy variant
          <input className="field-input cfg-input" type="text" value={variant} maxLength={80} disabled={busy} onChange={event => setVariant(event.target.value)} />
        </label>
        <label className="cfg-field">Purpose (optional)
          <input className="field-input cfg-input" type="text" value={purpose} maxLength={120} placeholder="Candidate · seeds 1–5" disabled={busy} onChange={event => setPurpose(event.target.value)} />
        </label>
      </div>
    </form>
    <Notice tone="info">Queueing adds the run to this workspace as Queued. No evaluation runner is connected to this workspace, so nothing executes it until a runner reports results.</Notice>
    {error && <p className="cd-error" role="alert">{error}</p>}
  </Modal>;
}

/* ---------- promote ---------- */

/** Confirms promoting a revision that passed its gate: it becomes the production revision and the production robots are recorded on it. */
export function PromoteDialog({ workspace, config, rev, canSave, save, onClose, onDone }: {
  workspace: ConvoyWorkspace; config: Configuration; rev: string; canSave: boolean; save: Save;
  onClose: () => void; onDone: (promoted: { rev: string; from: string | null; robots: number }) => void;
}) {
  const gate = revisionGate(workspace, config, rev);
  const [robots] = useState(() => robotsFor(workspace, config.id, { role: "production" }));
  const [from] = useState(config.productionRev);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const id = useId();
  const noteId = `${id}-note`;
  const reason = !canSave ? UNSAVABLE : !gate.passed ? `Promotion needs a passing gate: ${gate.reason}.` : null;
  const disabled = busy || reason !== null;
  async function promote() {
    if (disabled) return;
    setBusy(true);
    setError(null);
    const result = await save(current => promoteRevision(current, config.id, rev, Date.now()));
    setBusy(false);
    if (result.ok) onDone({ rev, from, robots: robots.length });
    else setError(result.error);
  }
  return <Modal open onClose={onClose} eyebrow={config.name} title={`Promote ${rev} to production`}
    footer={<>
      {reason && <span className="cd-foot-note" id={noteId}>{reason}</span>}
      <button className="btn btn-secondary cfg-btn" type="button" onClick={onClose} data-autofocus="">Cancel</button>
      <button className="btn btn-primary cfg-btn" type="button" onClick={() => void promote()} aria-disabled={disabled || undefined} aria-describedby={reason ? noteId : undefined}>{busy ? "Promoting…" : `Promote ${rev}`}</button>
    </>}>
    <GateSummary gate={gate} />
    <dl className="portal-facts cd-facts">
      <Fact label="Production revision" value={`${from ?? "None"} → ${rev}`} />
      <Fact label="Production robots" value={robots.length ? robots.map(robot => robot.name).join(", ") : "None attached"} detail={robots.length ? `${fmtCount(robots.length)} ${robots.length === 1 ? "robot is" : "robots are"} recorded on ${rev} after promotion` : undefined} />
    </dl>
    <p className="portal-context-note cd-note">Promotion updates this workspace: {rev} becomes the production revision and the production robots are recorded on it. It does not deploy anything to the robots.</p>
    {error && <p className="cd-error" role="alert">{error}</p>}
  </Modal>;
}

/* ---------- flags ---------- */

const SEVERITY: Record<FlagSeverity, { label: string; text: string }> = {
  warning: { label: "Warning", text: "Listed under warnings in the attention banner." },
  attention: { label: "Needs attention", text: "Listed first in the attention banner." },
};

/**
 * A robot's flags: those in effect (stored flags can be cleared; flags raised by a
 * rule on measured telemetry clear when the reading recovers) and a form for a
 * manual flag with the operator's note.
 */
export function FlagDialog({ row, canSave, save, by, now, onClose, onDone }: {
  row: RobotRow; canSave: boolean; save: Save; by: string; now: number | null; onClose: () => void; onDone: (label: string) => void;
}) {
  const { robot } = row;
  const [label, setLabel] = useState("");
  const [note, setNote] = useState("");
  const [severity, setSeverity] = useState<FlagSeverity>("warning");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const id = useId();
  const formId = `${id}-form`, noteId = `${id}-note`;
  const flags = row.readings.flags;
  const reason = !canSave ? UNSAVABLE : null;

  async function clear(flagId: string, flagLabel: string) {
    if (busy || reason) return;
    setBusy(flagId);
    setError(null);
    setStatus(null);
    const result = await save(current => clearFlag(current, robot.id, flagId, Date.now()));
    setBusy(null);
    if (result.ok) setStatus(`Cleared “${flagLabel}” on ${robot.name}.`);
    else setError(result.error);
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || reason) return;
    if (!label.trim() || !note.trim()) { setError("Give the flag a reason and a note."); return; }
    setBusy("add");
    setError(null);
    setStatus(null);
    const input = { label: label.trim(), note: note.trim(), severity, ...(by ? { by } : {}) };
    const result = await save(current => flagRobot(current, robot.id, input, Date.now()));
    setBusy(null);
    if (result.ok) onDone(input.label);
    else setError(result.error);
  }

  return <Modal open onClose={onClose} eyebrow={`${robot.role === "test" ? "Test" : "Production"} robot · ${robot.site}`} title={`Flags · ${robot.name}`} closeLabel="Close flags"
    footer={<>
      {reason && <span className="cd-foot-note" id={noteId}>{reason}</span>}
      <button className="btn btn-secondary cfg-btn" type="button" onClick={onClose}>Close</button>
      <button className="btn btn-primary cfg-btn" type="submit" form={formId} aria-disabled={!!busy || !!reason || undefined} aria-describedby={reason ? noteId : undefined}>{busy === "add" ? "Flagging…" : "Flag robot"}</button>
    </>}>
    <section className="cd-flagset" aria-labelledby={`${id}-current`}>
      <h3 id={`${id}-current`} className="cd-dialog-sub">In effect</h3>
      {flags.length ? <ul className="cd-flaglist">{flags.map(flag => <li key={flag.id}>
        <div className="cd-flaglist__head"><Badge tone="warning" icon="flag">{SEVERITY[flag.severity].label}</Badge><strong>{flag.label}</strong></div>
        <p className="cd-flaglist__detail">{flag.detail}</p>
        <div className="cd-flaglist__meta">
          <ProvenanceBadge provenance={flag.provenance} now={now} />
          <time dateTime={flag.at}>{fmtDateTime(flag.at)}</time>
          {flag.by && <span>by {flag.by}</span>}
          {flag.origin === "stored"
            ? <button className="cfg-btn-text" type="button" aria-disabled={!!busy || !!reason || undefined} onClick={() => void clear(flag.id, flag.label)}>{busy === flag.id ? "Clearing…" : "Clear flag"}<span className="cfg-sr"> “{flag.label}”</span></button>
            : <span>Raised by a flag rule on measured telemetry; it clears when the reading recovers.</span>}
        </div>
      </li>)}</ul> : <p className="portal-empty cd-empty">No flags on {robot.name}.</p>}
      <p className="cd-status" role="status">{status}</p>
    </section>
    <form id={formId} className="cd-form" onSubmit={event => void submit(event)} noValidate>
      <h3 className="cd-dialog-sub">Add a flag</h3>
      <label className="cfg-field">Reason
        <input className="field-input cfg-input" type="text" value={label} maxLength={80} required disabled={busy === "add"} onChange={event => setLabel(event.target.value)} data-autofocus="" />
        <span className="cfg-field__hint">A short label for the banner and the table, e.g. “Gripper slipping”.</span>
      </label>
      <label className="cfg-field">Note
        <textarea className="field-input cfg-input cd-textarea" value={note} maxLength={500} rows={3} required disabled={busy === "add"} onChange={event => setNote(event.target.value)} />
        <span className="cfg-field__hint">What you saw and what should happen next. Saved with your account name.</span>
      </label>
      <fieldset className="cfg-field" disabled={busy === "add"}>
        <legend>Severity</legend>
        <div className="cfg-choices">{(Object.keys(SEVERITY) as FlagSeverity[]).map(key => <label className="cfg-choice" key={key}>
          <input type="radio" name={`${id}-severity`} value={key} checked={severity === key} onChange={() => setSeverity(key)} />
          <span><span className="cfg-choice__title">{SEVERITY[key].label}</span><span className="cfg-choice__text">{SEVERITY[key].text}</span></span>
        </label>)}</div>
      </fieldset>
    </form>
    {error && <p className="cd-error" role="alert">{error}</p>}
  </Modal>;
}
