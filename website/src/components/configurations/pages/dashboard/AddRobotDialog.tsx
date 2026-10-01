"use client";

import Link from "next/link";
import { useId, useState } from "react";
import type { FormEvent } from "react";
import { unassignedRobots } from "@/lib/configurations/client";
import type { SaveResult, WorkspaceUpdate } from "@/lib/configurations/client";
import { revisionGate } from "@/lib/configurations/dashboard";
import type { RevisionGate } from "@/lib/configurations/dashboard";
import { fmtDate, fmtDateTime, fmtRelative } from "@/lib/configurations/format";
import { attachRobot } from "@/lib/configurations/mutations";
import { routes } from "@/lib/configurations/routes";
import type { Configuration, ConvoyWorkspace, Robot, RobotRole } from "@/lib/configurations/types";
import { ProvenanceBadge } from "../../Badges";
import { Notice } from "../../Notice";
import { Modal } from "../../Overlay";
import { revisionStatus } from "./Specification";

export type Save = (update: WorkspaceUpdate) => Promise<SaveResult>;

const PAIR = "pair-new-device";
/**
 * The existing agent enrollment flow (control-plane/docs/JETSON_GUIDE.md §3 and
 * scripts/jetson/install-agent.sh), with placeholders: the one-use token comes from
 * the control plane and is never shown or created here.
 */
export const ENROLL_COMMAND = [
  "sudo -u convoy-agent /opt/convoy-agent/venv/bin/convoy-agent enroll \\",
  "  --server https://<convoy-host> --token <token> --name <device-name>",
  "sudo systemctl start convoy-agent",
].join("\n");

/** The gate box: a passing gate with its criteria, or why production is blocked. */
export function GateSummary({ gate }: { gate: RevisionGate }) {
  // Revision ids stay lowercase ("r4 passed gate …"), as everywhere else.
  if (gate.passed && gate.run) {
    const shown = gate.lines.slice(0, 3).map(line => `${line.label}: ${line.actual}${line.target ? ` (${line.target})` : ""}`).join(" · ");
    const more = gate.lines.length - 3;
    return <Notice tone="success"><strong>{gate.reason}.</strong> {shown}{more > 0 ? ` · ${more} more ${more === 1 ? "criterion" : "criteria"} passed` : ""}. <ProvenanceBadge provenance={gate.run.provenance} /></Notice>;
  }
  return <Notice tone="warning" icon="blocked"><strong>Production needs a passing gate.</strong> {gate.reason}.{gate.run && <> <ProvenanceBadge provenance={gate.run.provenance} /></>}</Notice>;
}

function lastSeen(robot: Robot, now: number | null): string {
  if (!robot.lastSeenAt) return "no report yet";
  return `last seen ${now === null ? fmtDateTime(robot.lastSeenAt) : fmtRelative(robot.lastSeenAt, now)}`;
}

/** "Pair a new device": the existing enrollment steps, with a copy button. Nothing is enrolled from here. */
function PairingGuide() {
  const [copied, setCopied] = useState<string | null>(null);
  function copy() {
    void navigator.clipboard.writeText(ENROLL_COMMAND).then(() => setCopied("Command copied."), () => setCopied("Copy the command manually."));
  }
  return <div className="cd-pair">
    <p className="cd-pair__lead">Install the Convoy agent on the device (<code>scripts/jetson/install-agent.sh</code> in the Convoy repository), then enroll it once. Enrollment needs a one-use token that an operator creates in the control plane (Enroll device); it expires after 1 hour by default. On the device, run:</p>
    <pre className="console-code">{ENROLL_COMMAND}</pre>
    <div className="cd-pair__foot">
      <button className="cfg-btn-text" type="button" onClick={copy}>Copy command</button>
      <span role="status">{copied}</span>
      <Link className="cfg-btn-text" href={routes.applications()}>Simulators enroll from Applications <span aria-hidden="true">↗</span></Link>
    </div>
    <p className="cd-pair__lead">The device then reports under Devices &amp; inference. It can be added here once it is registered as a robot in this workspace.</p>
  </div>;
}

/**
 * Add robot: one of the registered robots that no configuration uses (or the
 * enrollment steps for a new device), a role, a site and a revision. Production
 * needs the latest gated suite run of that revision to have passed. Saving goes
 * through the workspace document (`attachRobot`); the dialog closes only after the
 * write is confirmed and shows the error otherwise.
 */
export function AddRobotDialog({ workspace, config, canSave, save, now, onClose, onDone }: {
  workspace: ConvoyWorkspace; config: Configuration; canSave: boolean; save: Save; now: number | null;
  onClose: () => void; onDone: (added: { robot: Robot; role: RobotRole; rev: string }) => void;
}) {
  // Fixed while the dialog is open: the optimistic save would otherwise remove the chosen robot from the list mid-save.
  const [candidates] = useState(() => unassignedRobots(workspace));
  const revisions = config.revisions.toReversed();
  const [choice, setChoice] = useState<string>(candidates[0]?.id ?? PAIR);
  const [role, setRole] = useState<RobotRole>("test");
  const [rev, setRev] = useState<string>(config.candidateRev ?? config.productionRev ?? revisions[0]?.rev ?? "");
  const [site, setSite] = useState<string>(candidates[0]?.site ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const id = useId();
  const formId = `${id}-form`, noteId = `${id}-note`, sitesId = `${id}-sites`;
  const robot = candidates.find(item => item.id === choice) ?? null;
  const pairing = choice === PAIR;
  const gate = revisionGate(workspace, config, rev);
  const blocked = role === "production" && !gate.passed;
  const sites = [...new Set(workspace.robots.map(item => item.site))].toSorted();
  const reason = !canSave ? "Changes cannot be saved while the stored workspace is unreadable."
    : pairing ? "Available once the device is enrolled and registered in this workspace."
      : !robot ? "Choose a robot to add."
        : blocked ? `Production is not available: ${gate.reason}.` : null;
  const disabled = busy || reason !== null;

  function pick(next: string) {
    setChoice(next);
    setError(null);
    const picked = candidates.find(item => item.id === next);
    if (picked) setSite(picked.site);
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (disabled || !robot) return;
    setBusy(true);
    setError(null);
    const input = { configId: config.id, role, site: site.trim() || robot.site, rev };
    const result = await save(current => attachRobot(current, robot.id, input, Date.now()));
    setBusy(false);
    if (result.ok) onDone({ robot, role, rev });
    else setError(result.error);
  }

  return <Modal open onClose={onClose} eyebrow={`${config.name} · ${rev}`} title={`Add robot to ${config.name}`}
    footer={<>
      {reason && <span className="cd-foot-note" id={noteId}>{reason}</span>}
      <button className="btn btn-secondary cfg-btn" type="button" onClick={onClose}>Cancel</button>
      <button className="btn btn-primary cfg-btn" type="submit" form={formId} aria-disabled={disabled || undefined} aria-describedby={reason ? noteId : undefined}>{busy ? "Adding…" : "Add robot"}</button>
    </>}>
    <form id={formId} className="cd-form" onSubmit={event => void submit(event)} noValidate>
      <fieldset className="cfg-field" disabled={busy}>
        <legend>Robot</legend>
        <div className={`cfg-choices${candidates.length >= 2 ? " cd-choices--3" : ""}`}>
          {candidates.map((item, i) => <label className="cfg-choice" key={item.id}>
            <input type="radio" name={`${id}-robot`} value={item.id} checked={choice === item.id} onChange={() => pick(item.id)} data-autofocus={i === 0 ? "" : undefined} />
            <span><span className="cfg-choice__title">{item.name}</span><span className="cfg-choice__text">{item.site} · registered {fmtDate(item.registeredAt)} · {lastSeen(item, now)}</span></span>
          </label>)}
          <label className="cfg-choice">
            <input type="radio" name={`${id}-robot`} value={PAIR} checked={pairing} onChange={() => pick(PAIR)} data-autofocus={candidates.length ? undefined : ""} />
            <span><span className="cfg-choice__title">Pair a new device</span><span className="cfg-choice__text">Enroll a device with the Convoy agent</span></span>
          </label>
        </div>
        <p className="cd-hint">{candidates.length ? "Registered robots that are not attached to a configuration" : "Every registered robot is attached to a configuration."}
          {candidates.length > 0 && workspace.meta.sample && <ProvenanceBadge provenance={{ kind: "sample" }} />}</p>
      </fieldset>

      {pairing ? <PairingGuide /> : <>
        <fieldset className="cfg-field" disabled={busy}>
          <legend>Role</legend>
          <div className="cfg-choices">
            <label className="cfg-choice">
              <input type="radio" name={`${id}-role`} value="test" checked={role === "test"} onChange={() => setRole("test")} />
              <span><span className="cfg-choice__title">Test</span><span className="cfg-choice__text">Runs evaluation suites in simulation and on bench hardware.</span></span>
            </label>
            <label className={`cfg-choice${gate.passed ? "" : " cd-choice--off"}`}>
              <input type="radio" name={`${id}-role`} value="production" checked={role === "production"} disabled={!gate.passed} onChange={() => setRole("production")} />
              <span><span className="cfg-choice__title">Production</span><span className="cfg-choice__text">Receives this revision; requires a passing eval gate.</span>
                {!gate.passed && <span className="cd-choice__reason">Not available: {gate.reason}.</span>}</span>
            </label>
          </div>
        </fieldset>
        {role === "production" ? <GateSummary gate={gate} />
          : <p className="portal-context-note cd-note">Test robots need no gate. They run evaluation suites on {rev} and receive no production work.</p>}
        <div className="cd-row">
          <label className="cfg-field">Site
            <input className="field-input cfg-input" type="text" value={site} list={sitesId} maxLength={120} disabled={busy} onChange={event => setSite(event.target.value)} />
            <span className="cfg-field__hint">Shown in the robots table.</span>
          </label>
          <datalist id={sitesId}>{sites.map(item => <option key={item} value={item} />)}</datalist>
          <label className="cfg-field">Revision
            <select className="field-input cfg-input" value={rev} disabled={busy} onChange={event => setRev(event.target.value)}>
              {revisions.map(item => { const status = revisionStatus(config, item.rev); return <option key={item.rev} value={item.rev}>{item.rev}{status ? ` · ${status}` : ""}</option>; })}
            </select>
            <span className="cfg-field__hint">Production robots need a revision that passed its gate.</span>
          </label>
        </div>
      </>}
      {error && <p className="cd-error" role="alert">{error}</p>}
    </form>
  </Modal>;
}
