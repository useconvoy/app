"use client";

import { useId, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { ProvenanceBadge } from "@/components/configurations/Badges";
import { FactList } from "@/components/configurations/Facts";
import { Notice } from "@/components/configurations/Notice";
import { Modal } from "@/components/configurations/Overlay";
import { useSession } from "@/components/configurations/Session";
import { SelectField } from "@/components/configurations/Toolbar";
import { useWorkspace } from "@/lib/configurations/client";
import { fmtCount, fmtDateTime, fmtRatio, fmtShare, runLabel } from "@/lib/configurations/format";
import { clearFlag, flagRobot, queueEvaluation, setRobotRole } from "@/lib/configurations/mutations";
import { activeRunOn, evaluationChoices, gateSummary, VARIANT_LABEL } from "@/lib/configurations/robot";
import { getRevision, getSuite, successShare } from "@/lib/configurations/selectors";
import type { Configuration, ConvoyWorkspace, EvalRun, FlagSeverity, Robot, RobotRole } from "@/lib/configurations/types";

/** What the page reports after a confirmed save (the PUT succeeded). */
export interface SavedChange { text: ReactNode; action?: ReactNode; moveFocus?: boolean }

/** Submit state shared by the dialogs: a running flag and the last save error, shown inside the dialog. */
function useSubmit() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function run(save: () => Promise<{ ok: true } | { ok: false; error: string }>, done: () => void) {
    if (busy) return;
    setBusy(true); setError(null);
    const result = await save();
    setBusy(false);
    if (result.ok) done(); else setError(result.error);
  }
  return { busy, error, setError, run };
}

function Footer({ formId, busy, label, busyLabel, onCancel, disabled }: { formId: string; busy: boolean; label: string; busyLabel: string; onCancel: () => void; disabled?: boolean }) {
  return <>
    <button className="btn btn-secondary cfg-btn" type="button" onClick={onCancel}>Cancel</button>
    <button className="btn btn-primary cfg-btn" type="submit" form={formId} disabled={busy || disabled}>{busy ? busyLabel : label}</button>
  </>;
}

/* ---------- Flag ---------- */

const SEVERITIES: ReadonlyArray<[FlagSeverity, string, string]> = [
  ["warning", "Warning", "Marks the robot degraded; it keeps working."],
  ["attention", "Needs attention", "Raises the robot to the top of the attention list."],
];

/** A manual flag with the operator's reason and note; saved to the workspace document. */
export function FlagDialog({ robot, onClose, onSaved }: { robot: Robot; onClose: () => void; onSaved: (change: SavedChange) => void }) {
  const ws = useWorkspace();
  const session = useSession();
  const formId = useId();
  const [label, setLabel] = useState("");
  const [note, setNote] = useState("");
  const [severity, setSeverity] = useState<FlagSeverity>("warning");
  const submit = useSubmit();
  function onSubmit(event: FormEvent) {
    event.preventDefault();
    const reason = label.trim(), text = note.trim();
    if (!reason || !text) { submit.setError("Add a reason and a note before flagging."); return; }
    const at = Date.now();
    void submit.run(() => ws.save(current => flagRobot(current, robot.id, { label: reason, note: text, severity, by: session.email }, at)),
      () => onSaved({ text: <><strong>{robot.name}</strong> flagged: {reason}. The flag is saved in the workspace document.</> }));
  }
  return <Modal open onClose={onClose} eyebrow={robot.name} title={`Flag ${robot.name}`} footer={<Footer formId={formId} busy={submit.busy} label="Flag robot" busyLabel="Saving…" onCancel={onClose} />}>
    <form id={formId} className="rb-form" onSubmit={onSubmit} noValidate>
      <label className="cfg-field">Reason<input className="field-input cfg-input" data-autofocus type="text" maxLength={80} required value={label} onChange={event => setLabel(event.target.value)} placeholder="e.g. Gripper noise on close" /><span className="cfg-field__hint">Shown with the robot in tables and on this page.</span></label>
      <label className="cfg-field">Note<textarea className="field-input cfg-input rb-textarea" required rows={3} maxLength={600} value={note} onChange={event => setNote(event.target.value)} placeholder="What you saw and what should happen next" /></label>
      <fieldset className="cfg-field"><legend>Severity</legend><div className="cfg-choices">
        {SEVERITIES.map(([value, title, text]) =>
          <label key={value} className={`cfg-choice${severity === value ? " cfg-choice--on" : ""}`}><input type="radio" name={`${formId}-severity`} value={value} checked={severity === value} onChange={() => setSeverity(value)} /><span><span className="cfg-choice__title">{title}</span><span className="cfg-choice__text">{text}</span></span></label>)}
      </div></fieldset>
      <p className="rb-form__note">Saved with your account, {session.email}, and the time. Flags change the workspace document only; nothing is sent to the robot.</p>
      {submit.error && <p className="rb-form__error" role="alert">{submit.error}</p>}
    </form>
  </Modal>;
}

const newestFirst = <T extends { at: string }>(items: readonly T[]) => items.toSorted((a, b) => Date.parse(b.at) - Date.parse(a.at));

/** Clears one of the robot's stored flags (rule flags on measured telemetry clear themselves). */
export function ClearFlagDialog({ robot, onClose, onSaved }: { robot: Robot; onClose: () => void; onSaved: (change: SavedChange) => void }) {
  const ws = useWorkspace();
  const formId = useId();
  const flags = newestFirst(robot.flags);
  const [flagId, setFlagId] = useState(flags[0]?.id ?? "");
  const submit = useSubmit();
  const chosen = flags.find(flag => flag.id === flagId) ?? null;
  // Clearing the last flag removes the "Clear flag" button that opened this dialog, so focus moves to the result.
  const last = flags.length === 1;
  // Rendered before the handler is declared: the React compiler then keeps the handler out of the list's render-time work.
  const options = flags.map((flag, i) => <label key={flag.id} className={`cfg-choice${flag.id === flagId ? " cfg-choice--on" : ""}`}>
    <input type="radio" name={`${formId}-flag`} value={flag.id} checked={flag.id === flagId} onChange={() => setFlagId(flag.id)} data-autofocus={i === 0 ? true : undefined} />
    <span><span className="cfg-choice__title">{flag.label}</span><span className="cfg-choice__text">{flag.detail}</span>
      <span className="cfg-choice__text rb-prov"><ProvenanceBadge provenance={flag.provenance} /> {flag.by ? `Flagged by ${flag.by} · ` : ""}{fmtDateTime(flag.at, robot.clock)}</span></span>
  </label>);
  function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!chosen) return;
    const at = Date.now();
    void submit.run(() => ws.save(current => clearFlag(current, robot.id, chosen.id, at)),
      () => onSaved({ moveFocus: last, text: <>Cleared “{chosen.label}” on <strong>{robot.name}</strong>. The change is saved in the workspace document.</> }));
  }
  return <Modal open onClose={onClose} eyebrow={robot.name} title={flags.length > 1 ? "Clear a flag" : "Clear flag"} footer={<Footer formId={formId} busy={submit.busy} label="Clear flag" busyLabel="Clearing…" onCancel={onClose} disabled={!chosen} />}>
    <form id={formId} className="rb-form" onSubmit={onSubmit}>
      <fieldset className="cfg-field"><legend>{flags.length > 1 ? "Flag to clear" : "Flag"}</legend><div className="rb-choices">{options}</div></fieldset>
      <p className="rb-form__note">Clearing removes the flag from the workspace document. If a flag rule still matches the robot’s reported values, the rule raises it again.</p>
      {submit.error && <p className="rb-form__error" role="alert">{submit.error}</p>}
    </form>
  </Modal>;
}

/* ---------- Promote / move to test ---------- */

/** Confirms a role change. Promotion shows the gate run that allows it. */
export function RoleDialog({ robot, configuration, to, gateRun, onClose, onSaved }: { robot: Robot; configuration: Configuration; to: RobotRole; gateRun: EvalRun | null; onClose: () => void; onSaved: (change: SavedChange) => void }) {
  const ws = useWorkspace();
  const formId = useId();
  const submit = useSubmit();
  const promote = to === "production";
  function onSubmit(event: FormEvent) {
    event.preventDefault();
    const at = Date.now();
    void submit.run(() => ws.save(current => setRobotRole(current, robot.id, to, at)),
      () => onSaved({ moveFocus: true, text: promote
        ? <><strong>{robot.name}</strong> is now a production robot of {configuration.name} in the workspace document. Nothing was deployed to the robot from this page.</>
        : <><strong>{robot.name}</strong> moved to test in the workspace document. Nothing changed on the robot.</> }));
  }
  const share = gateRun ? successShare(gateRun) : null;
  return <Modal open onClose={onClose} eyebrow={`${robot.name} · ${configuration.name} ${robot.rev ?? ""}`.trim()} title={promote ? `Promote ${robot.name} to production` : `Move ${robot.name} to test`}
    footer={<Footer formId={formId} busy={submit.busy} label={promote ? "Promote to production" : "Move to test"} busyLabel="Saving…" onCancel={onClose} />}>
    <form id={formId} className="rb-form" onSubmit={onSubmit}>
      {promote && gateRun && <Notice tone="info" icon="check">{robot.rev} passed the gate on {runLabel(gateRun)}: {fmtShare(share)} ({fmtRatio(gateRun.counts.successes, gateRun.counts.episodes)}){gateRun.safety ? ` · ${fmtCount(gateRun.safety.critical)} critical safety events` : ""}. <ProvenanceBadge provenance={gateRun.provenance} /></Notice>}
      <p className="rb-form__text">{promote
        ? `${robot.name} becomes a production robot of ${configuration.name}, running ${robot.rev}. Production robots appear in the configuration’s production figures.`
        : `${robot.name} stops counting as a production robot of ${configuration.name}; it keeps ${robot.rev ?? "its revision"} and joins the test robots.`}</p>
      <p className="rb-form__note">This updates the workspace document only; {promote ? "nothing is deployed to the robot" : "nothing changes on the robot"}.</p>
      {submit.error && <p className="rb-form__error" role="alert">{submit.error}</p>}
    </form>
  </Modal>;
}

/* ---------- Run evaluation ---------- */

/**
 * Queues a suite run on the robot in the workspace document. No evaluation
 * runner is connected to the document, so the run stays Queued; the dialog and
 * the result say so instead of showing progress that is not happening.
 */
export function RunEvaluationDialog({ workspace, configuration, robot, onClose, onQueued }: { workspace: ConvoyWorkspace; configuration: Configuration; robot: Robot; onClose: () => void; onQueued: (run: EvalRun) => void }) {
  const ws = useWorkspace();
  const formId = useId();
  const choices = evaluationChoices(workspace, configuration, robot);
  const [suiteId, setSuiteId] = useState(choices.suiteId ?? "");
  const [rev, setRev] = useState(choices.rev ?? "");
  const [purpose, setPurpose] = useState("");
  const submit = useSubmit();
  const suite = getSuite(workspace, suiteId);
  const revision = getRevision(configuration, rev);
  const variant = revision ? VARIANT_LABEL[revision.routing.mode] : null;
  const active = activeRunOn(workspace, robot.id);
  const gate = gateSummary(suite);
  const blocked = !suite ? "Add an evaluation suite to the workspace before queuing a run." : !revision ? "Choose a revision of this configuration." : null;
  function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (blocked || !variant) { submit.setError(blocked); return; }
    const at = Date.now();
    let queued: EvalRun | null = null;
    void submit.run(() => ws.save(current => {
      const next = queueEvaluation(current, { configId: configuration.id, rev, robotId: robot.id, suiteId, variant, ...(purpose.trim() ? { purpose: purpose.trim() } : {}) }, at);
      queued = next.run;
      return next.workspace;
    }), () => { if (queued) onQueued(queued); });
  }
  return <Modal open onClose={onClose} eyebrow={`${robot.name} · ${configuration.name}`} title={`Run evaluation on ${robot.name}`}
    footer={<Footer formId={formId} busy={submit.busy} label="Queue run" busyLabel="Queuing…" onCancel={onClose} disabled={!!blocked} />}>
    <form id={formId} className="rb-form" onSubmit={onSubmit}>
      <label className="cfg-field">Evaluation suite
        <select className="field-input cfg-input" data-autofocus value={suiteId} onChange={event => setSuiteId(event.target.value)}>
          {choices.suites.length ? choices.suites.map(item => <option key={item.id} value={item.id}>{item.label} · {item.detail}</option>) : <option value="" disabled>No suites in this workspace</option>}
        </select>
        {gate && <span className="cfg-field__hint">Gate: {gate}.</span>}
      </label>
      <SelectField label="Configuration revision" value={rev} onChange={setRev} options={choices.revisions.map(item => ({ value: item.rev, label: item.label }))} />
      <FactList className="rb-facts rb-dialog-facts" facts={[
        { label: "Policy variant", value: variant ?? "Not reported", detail: revision?.routing.summary },
        { label: "Episodes", value: suite ? `${fmtCount(suite.episodesPerRun)} per run` : "Not reported", detail: suite ? `${fmtCount(suite.seedsPerCell)} seeds per cell · ${suite.simEngine}` : undefined },
      ]} />
      {suite && <p className="rb-form__note">Timing: {suite.timing}.</p>}
      <label className="cfg-field">Purpose (optional)<input className="field-input cfg-input" type="text" maxLength={80} value={purpose} onChange={event => setPurpose(event.target.value)} placeholder="e.g. Confirmation · seeds 6–10" /></label>
      <Notice tone="info">Queuing saves the run to the workspace document. <strong>No evaluation runner is connected</strong>, so the run stays queued and reports no results until one is.{active ? ` ${runLabel(active)} is ${active.status === "running" && active.progress ? `running on ${robot.name} (${fmtCount(active.progress.done)} of ${fmtCount(active.progress.total)} episodes)` : `already queued on ${robot.name}`}.` : ""}</Notice>
      {submit.error && <p className="rb-form__error" role="alert">{submit.error}</p>}
    </form>
  </Modal>;
}
