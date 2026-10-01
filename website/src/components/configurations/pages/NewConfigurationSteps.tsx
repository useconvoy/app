"use client";

import Link from "next/link";
import { useId } from "react";
import type { ReactNode } from "react";
import { currentRevision, getConfiguration, listConfigurations, nextRevision, robotHref, robotsFor } from "@/lib/configurations/client";
import {
  describeAction, describeEscalation, describeThermal, describeTrigger, ESTIMATE, MODEL_ROLE_LABEL, MODEL_STATE_LABEL, modelKey, onDevicePolicies, residentMemory,
  ROUTE_LABEL, safetyRange, selectedPowerMode, setCloudModel, setHardware, setPowerMode, setRobot, setRouteMode, setSaveAs, setTwin, startFrom, toggleCloudModel,
  toggleEdgeModel, twinKey, updateRouting,
} from "@/lib/configurations/draft";
import type { ConfigurationDraft, DraftCatalog, DraftStep, SaveAs } from "@/lib/configurations/draft";
import { fmtFixed, fmtNumber, fmtUnit } from "@/lib/configurations/format";
import type { LiveBinding } from "@/lib/configurations/live";
import type { CloudModel, ConvoyWorkspace, EdgeModel, ModelRole, ModelState, Provenance, Robot, RouteMode, SafetyDefinition, Severity } from "@/lib/configurations/types";
import { Badge, NotReported, ProvenanceBadge } from "../Badges";
import type { BadgeTone } from "../Badges";
import { DataTable } from "../DataTable";
import { FactList, Panel } from "../Facts";
import type { FactItem } from "../Facts";
import { Icon } from "../Icons";
import { Notice } from "../Notice";

/* ---------- fields (label, control, hint and error linked by id) ---------- */

const describedBy = (id: string, hint: unknown, errors: readonly string[]) => [hint ? `${id}-hint` : null, errors.length ? `${id}-error` : null].filter(Boolean).join(" ") || undefined;
function FieldNotes({ id, hint, errors }: { id: string; hint?: ReactNode; errors: readonly string[] }) {
  return <>{hint && <span className="cfg-field__hint" id={`${id}-hint`}>{hint}</span>}{errors.length > 0 && <span className="ci-error" id={`${id}-error`}>{errors.join(" ")}</span>}</>;
}

export function TextInput({ label, value, onChange, hint, errors = [], multiline = false, span }: { label: string; value: string; onChange: (value: string) => void; hint?: ReactNode; errors?: readonly string[]; multiline?: boolean; span?: "wide" | "two" }) {
  const id = useId();
  const described = describedBy(id, hint, errors);
  return <div className={`cfg-field${span === "wide" ? " ci-wide" : span === "two" ? " ci-span-2" : ""}`}>
    <label htmlFor={`${id}-input`}>{label}</label>
    {multiline
      ? <textarea id={`${id}-input`} className="field-input cfg-input ci-textarea" rows={2} value={value} aria-invalid={errors.length ? true : undefined} aria-describedby={described} onChange={event => onChange(event.target.value)} />
      : <input id={`${id}-input`} className="field-input cfg-input" type="text" autoComplete="off" value={value} aria-invalid={errors.length ? true : undefined} aria-describedby={described} onChange={event => onChange(event.target.value)} />}
    <FieldNotes id={id} hint={hint} errors={errors} />
  </div>;
}

/** A number or nothing: an empty field is null (the rule is off), never 0. */
export function NumberInput({ label, value, onChange, hint, errors = [], max, step = "any" }: { label: string; value: number | null; onChange: (value: number | null) => void; hint?: ReactNode; errors?: readonly string[]; max?: number; step?: number | "any" }) {
  const id = useId();
  return <div className="cfg-field">
    <label htmlFor={`${id}-input`}>{label}</label>
    <input id={`${id}-input`} className="field-input cfg-input" type="number" inputMode="decimal" min={0} max={max} step={step} value={value ?? ""}
      aria-invalid={errors.length ? true : undefined} aria-describedby={describedBy(id, hint, errors)}
      onChange={event => { const next = event.target.valueAsNumber; onChange(Number.isFinite(next) ? next : null); }} />
    <FieldNotes id={id} hint={hint} errors={errors} />
  </div>;
}

export function SelectInput({ label, value, options, onChange, hint, errors = [] }: { label: string; value: string; options: ReadonlyArray<{ value: string; label: string }>; onChange: (value: string) => void; hint?: ReactNode; errors?: readonly string[] }) {
  const id = useId();
  return <div className="cfg-field">
    <label htmlFor={`${id}-input`}>{label}</label>
    <select id={`${id}-input`} className="field-input cfg-input" value={value} aria-invalid={errors.length ? true : undefined} aria-describedby={describedBy(id, hint, errors)} onChange={event => onChange(event.target.value)}>
      {options.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
    </select>
    <FieldNotes id={id} hint={hint} errors={errors} />
  </div>;
}

export interface ChoiceOption { value: string; title: ReactNode; text?: ReactNode; tags?: ReactNode; checked: boolean; disabled?: boolean }
/** Radio or checkbox cards (`cfg-choice`) in a fieldset with its legend; the whole card is the label. */
export function ChoiceGroup({ legend, type, options, onChange, errors = [], columns = 2, children }: { legend: string; type: "radio" | "checkbox"; options: readonly ChoiceOption[]; onChange: (value: string, checked: boolean) => void; errors?: readonly string[]; columns?: 2 | 3; children?: ReactNode }) {
  const id = useId();
  return <fieldset className="cfg-field" aria-describedby={errors.length ? `${id}-error` : undefined}>
    <legend>{legend}</legend>
    <div className={`cfg-choices${columns === 3 ? " ci-choices--3" : ""}`}>
      {options.map(option => <label key={option.value} className={`cfg-choice${option.disabled ? " ci-choice--off" : ""}`}>
        <input type={type} name={`${id}-choice`} value={option.value} checked={option.checked} disabled={option.disabled} onChange={event => onChange(option.value, event.target.checked)} />
        <span>
          <span className="cfg-choice__title">{option.title}</span>
          {option.text && <span className="cfg-choice__text">{option.text}</span>}
          {option.tags && <span className="ci-choice__tags">{option.tags}</span>}
        </span>
      </label>)}
    </div>
    {errors.length > 0 && <span className="ci-error" id={`${id}-error`}>{errors.join(" ")}</span>}
    {children}
  </fieldset>;
}

/** A titled group inside a step (hairline above, h3 and a note on the right). */
export function Group({ title, note, children }: { title: string; note?: ReactNode; children: ReactNode }) {
  return <div className="ci-group"><div className="ci-group__head"><h3>{title}</h3>{note && <span className="ci-group__note">{note}</span>}</div>{children}</div>;
}

/* ---------- shared bits ---------- */

const STATE_TONE: Record<ModelState, BadgeTone> = { active: "success", "fallback-only": "warning", proposed: "neutral", blocked: "warning", "not-deployed": "neutral" };
const SEVERITY_LABEL: Record<Severity, string> = { minor: "Minor", major: "Major", critical: "Critical" };
const sentence = (...parts: Array<string | null | undefined>) => {
  const text = parts.filter(Boolean).map(part => part!.trim().replace(/\.$/, "")).join(". ");
  return text ? `${text}.` : "";
};

function ModelTags({ model, evidence, now }: { model: EdgeModel | CloudModel; evidence?: Provenance | null; now: number | null }) {
  return <><Badge tone={STATE_TONE[model.state]}>{MODEL_STATE_LABEL[model.state]}</Badge>{evidence && <ProvenanceBadge provenance={evidence} now={now} />}</>;
}

/** The live device bound to a robot on this hardware: identity and contact, never stored or used by the check. */
export function ConnectedDevice({ robot, binding, now }: { robot: Robot; binding: LiveBinding | undefined; now: number | null }) {
  const data = binding?.data ?? null;
  const href = robotHref(robot);
  const identity = data ? [data.hardware?.model ?? data.name, data.hardware?.l4tRelease ? `L4T ${data.hardware.l4tRelease}` : null, data.agentVersion ? `agent ${data.agentVersion}` : null].filter(Boolean).join(" · ") : null;
  const state = data ? `${identity}, ${data.online ? "reporting now" : "not reporting right now"}.` : binding?.status === "loading" ? "Waiting for its first device report." : "No device report is available right now.";
  return <div className="ci-device" role="note">
    <Icon name={data?.online ? "check" : "info"} />
    <p><strong>Connected device: {robot.name}.</strong> {state} {href ? <><Link href={href}>Its robot page</Link> shows live readings;</> : "Live readings stay on the device view;"} the compatibility check uses stored evidence only.</p>
    <ProvenanceBadge provenance={data?.provenance ?? null} now={now} />
  </div>;
}

export interface StepProps {
  workspace: ConvoyWorkspace;
  catalog: DraftCatalog;
  draft: ConfigurationDraft;
  update: (change: (draft: ConfigurationDraft) => ConfigurationDraft) => void;
  /** Messages for a field, once its step has been checked. */
  errors: (field: string) => string[];
  /** Steps that resolve a field's problems when that is another step (from `DraftIssue.fix`). */
  fixes: (field: string) => DraftStep[];
  now: number | null;
  go: (step: DraftStep) => void;
}

/* ---------- 01 Robot ---------- */

export function RobotStep({ workspace, catalog, draft, update, errors, now }: StepProps) {
  const base = getConfiguration(workspace, draft.baseId);
  const nextRev = base ? nextRevision(base) : "r1";
  const running = base ? [...new Set(robotsFor(workspace, base.id).map(robot => robot.rev).filter((rev): rev is string => !!rev))].toSorted() : [];
  const twins = catalog.twins.filter(item => item.robot === draft.robot.name);
  const twin = draft.robot.simTwin;
  const twinOptions: ChoiceOption[] = [
    ...twins.map(item => ({ value: item.key, title: `${item.value.name} · ${item.value.engine}`, text: item.value.note ?? `Runs evaluation suites in ${item.value.engine}.`, checked: !!twin && twinKey(twin) === item.key })),
    ...(twin && !twins.some(item => item.key === twinKey(twin)) ? [{ value: twinKey(twin), title: `${twin.name} · ${twin.engine}`, text: twin.note, checked: true }] : []),
    { value: "", title: "No simulation twin", text: "Without a twin, evaluation suites cannot run in simulation.", checked: !twin },
  ];
  const robot = draft.robot;
  return <>
    <div className="ci-fields">
      <SelectInput label="Start from" value={draft.baseId} onChange={id => update(current => startFrom(workspace, current, id))}
        options={listConfigurations(workspace).map(config => ({ value: config.id, label: `${config.name} · ${currentRevision(config).rev}` }))}
        hint="Steps 02–05 start from its choices; picking another replaces them." />
      <SelectInput label="Robot" value={robot.name} onChange={name => update(current => setRobot(catalog, current, name))}
        options={[...new Set([robot.name, ...catalog.robots.map(item => item.key)])].map(name => ({ value: name, label: name }))} hint={robot.details} />
    </div>
    <ChoiceGroup legend="Save as" type="radio" errors={errors("saveAs")} onChange={value => update(current => setSaveAs(workspace, current, value as SaveAs))} options={[
      ...(base ? [{ value: "revision", title: `New revision ${nextRev} of ${base.name}`, text: running.length ? `Robots on ${running.join(" and ")} keep running until you move them to ${nextRev}.` : `${nextRev} becomes the revision under test.`, checked: draft.saveAs === "revision" }] : []),
      { value: "configuration", title: "New configuration", text: "A separate configuration, saved as a draft with revision r1.", checked: draft.saveAs === "configuration" },
    ]} />
    <div className="ci-fields">
      <TextInput label="Configuration name" value={draft.name} onChange={name => update(current => ({ ...current, name }))} errors={errors("name")}
        hint={draft.saveAs === "revision" ? "Renaming applies to every revision of this configuration." : "Shown on its card and dashboard."} />
      <TextInput label="Revision note" value={draft.note} onChange={note => update(current => ({ ...current, note }))} hint="Optional. What changed, e.g. a new policy version." />
      <TextInput label="Purpose" span="wide" multiline value={draft.purpose} onChange={purpose => update(current => ({ ...current, purpose }))} hint="One or two sentences on what this configuration is for." />
    </div>
    <ChoiceGroup legend="Simulation twin" type="radio" options={twinOptions} onChange={value => update(current => setTwin(catalog, current, value || null))} />
    <Group title="Sensors and control" note={<>Declared for this robot <ProvenanceBadge provenance={robot.provenance} now={now} /></>}>
      <FactList facts={[
        { label: "Robot", value: robot.summary },
        { label: "Cameras", value: robot.cameras.length ? robot.cameras.join(" · ") : <NotReported /> },
        { label: "Control rate", value: robot.controlRateHz === null ? <NotReported /> : `${fmtNumber(robot.controlRateHz)} Hz` },
        { label: "Action space", value: robot.actionSpace },
        ...(robot.safetyController ? [{ label: "Safety controller", value: robot.safetyController }] : []),
      ]} />
    </Group>
  </>;
}

/* ---------- 02 Edge hardware ---------- */

export function HardwareStep({ catalog, draft, update, now, connected, live }: StepProps & { connected: readonly Robot[]; live: Readonly<Record<string, LiveBinding | undefined>> }) {
  const hardware = draft.edgeHardware;
  const thermal = hardware.thermal;
  return <>
    <SelectInput label="Edge device" value={hardware.name} onChange={name => update(current => setHardware(catalog, current, name))}
      options={[...new Set([hardware.name, ...catalog.hardware.map(item => item.key)])].map(name => ({ value: name, label: name }))} />
    {connected[0] && <ConnectedDevice robot={connected[0]} binding={live[connected[0].id]} now={now} />}
    <ChoiceGroup legend="Power mode" type="radio" columns={3} onChange={id => update(current => setPowerMode(current, id))} options={hardware.powerModes.map(mode => ({
      value: mode.id, title: `${mode.label}${mode.recommended ? " · recommended" : ""}`, checked: mode.id === hardware.powerModeId,
      text: sentence(mode.capW === null ? "No power cap" : `Caps board input at ${fmtNumber(mode.capW)} W`, mode.note),
    }))} />
    <Group title="Device limits" note="Declared for this hardware">
      <FactList facts={[
        { label: "Memory", value: `${fmtFixed(hardware.memoryGiB)} GiB usable`, detail: hardware.memoryNote },
        ...(hardware.compute ? [{ label: "Compute", value: hardware.compute }] : []),
        { label: "Thermal limits", value: `Software throttle ${fmtUnit(thermal.swThrottleC, "°C", 1, true)} · hardware throttle ${fmtUnit(thermal.hwThrottleC, "°C", 1, true)} · shutdown ${fmtUnit(thermal.shutdownC, "°C", 1, true)}`, detail: thermal.sensor },
        ...(hardware.software ? [{ label: "Software", value: hardware.software }] : []),
      ]} />
    </Group>
  </>;
}

/* ---------- 03 Edge models ---------- */

export function EdgeStep({ catalog, draft, update, errors, now }: StepProps) {
  const hardware = draft.edgeHardware;
  const selected = new Set(draft.edgeModels.map(modelKey));
  const memory = residentMemory(draft);
  const free = memory.total === null ? null : hardware.memoryGiB - memory.total;
  const options = catalog.edgeModels.map(item => ({
    value: item.key, title: `${MODEL_ROLE_LABEL[item.value.role]} · ${item.value.name}`, text: sentence(item.value.runtime, item.value.detail),
    tags: <ModelTags model={item.value} evidence={item.hardware === hardware.name ? item.value.evidence : null} now={now} />,
    checked: selected.has(item.key), disabled: item.value.state === "blocked" && !selected.has(item.key),
  }));
  return <>
    <ChoiceGroup legend={`Models on the ${hardware.name}`} type="checkbox" options={options} errors={errors("edgeModels")} onChange={(key, on) => update(current => toggleEdgeModel(catalog, current, key, on))} />
    <Group title="Resident memory" note={memory.total === null
      ? <>Not reported for {memory.missing.map(model => model.shortName).join(", ")}</>
      : <>{fmtFixed(memory.total)} of {fmtFixed(hardware.memoryGiB)} GiB · {free !== null && free >= 0 ? `${fmtFixed(free)} GiB left for the system, agent and cameras` : `${fmtFixed(-(free ?? 0))} GiB over`} <ProvenanceBadge provenance={ESTIMATE} now={now} /></>}>
      {draft.edgeModels.length
        ? <ul className="cfg-hbars cfg-series--edge" aria-label={`Resident memory by model, GiB of ${fmtFixed(hardware.memoryGiB)} GiB usable`}>
          {draft.edgeModels.map(model => <li key={modelKey(model)} className="cfg-hbar">
            <span>{model.shortName}</span>
            <span className="cfg-hbar__track" aria-hidden="true">{model.residentGiB !== null && <span className="cfg-hbar__fill" style={{ width: `${Math.round(Math.min(model.residentGiB / hardware.memoryGiB, 1) * 1000) / 10}%` }} />}</span>
            <span className="cfg-hbar__value">{model.residentGiB === null ? <NotReported /> : `${fmtFixed(model.residentGiB)} GiB`}</span>
          </li>)}
        </ul>
        : <p className="ci-text">No edge models are selected.</p>}
      <p className="portal-context-note">Each bar is a model’s share of the {fmtFixed(hardware.memoryGiB)} GiB that CPU and GPU share, from its declared resident memory. Estimates, not measurements.</p>
    </Group>
  </>;
}

/* ---------- 04 Cloud models ---------- */

export function CloudStep({ catalog, draft, update, now }: StepProps) {
  const roleOptions = (role: ModelRole, none: { title: string; text: string }): ChoiceOption[] => {
    const current = draft.cloudModels.find(model => model.role === role);
    return [
      ...catalog.cloudModels.filter(item => item.value.role === role).map(item => ({
        value: item.key, title: item.value.name, checked: !!current && modelKey(current) === item.key,
        text: sentence(item.value.detail, item.value.chunk ? `${fmtNumber(item.value.chunk.actions)} actions at ${fmtNumber(item.value.chunk.rateHz)} Hz per chunk, valid for ${fmtNumber(item.value.chunk.validityMs)} ms` : null),
        tags: <ModelTags model={item.value} evidence={item.value.evidence} now={now} />,
      })),
      { value: "", ...none, checked: !current },
    ];
  };
  const others = catalog.cloudModels.filter(item => item.value.role !== "policy" && item.value.role !== "verifier");
  const { mode, fallbackTrigger, fallbackAction } = draft.routing;
  const budget = mode === "hybrid" && fallbackTrigger.cloudRttP95Ms !== null ? `the ${fmtNumber(fallbackTrigger.cloudRttP95Ms, 0)} ms fallback trigger` : fallbackAction.validityMs !== null ? `the ${fmtNumber(fallbackAction.validityMs, 0)} ms action validity` : "the budgets";
  return <>
    {mode === "edge-only" && <Notice tone="info">The route in step 05 is <strong>Edge only</strong>, so this configuration uses no cloud models. Set both to None here, or choose another route.</Notice>}
    <ChoiceGroup legend="Motor policy" type="radio" onChange={key => update(current => setCloudModel(catalog, current, "policy", key || null))}
      options={roleOptions("policy", { title: "No cloud policy", text: "Every motion comes from the edge." })} />
    <ChoiceGroup legend="Verifier" type="radio" onChange={key => update(current => setCloudModel(catalog, current, "verifier", key || null))}
      options={roleOptions("verifier", { title: "No verifier", text: "Nothing in the cloud checks progress or success." })} />
    {others.length > 0 && <ChoiceGroup legend="Other cloud models" type="checkbox" onChange={(key, on) => update(current => toggleCloudModel(catalog, current, key, on))}
      options={others.map(item => ({ value: item.key, title: `${MODEL_ROLE_LABEL[item.value.role]} · ${item.value.name}`, text: sentence(item.value.detail), checked: draft.cloudModels.some(model => modelKey(model) === item.key) }))} />}
    <Group title="Serving" note="Declared for each cloud model">
      {draft.cloudModels.length
        ? <FactList facts={draft.cloudModels.map(model => ({ label: `${MODEL_ROLE_LABEL[model.role]} · ${model.shortName}`, value: model.serving ?? <NotReported />,
          detail: model.chunk ? `${fmtNumber(model.chunk.actions)} actions at ${fmtNumber(model.chunk.rateHz)} Hz per chunk · valid for ${fmtNumber(model.chunk.validityMs)} ms` : undefined }))} />
        : <p className="ci-text">No cloud models are selected.</p>}
      <p className="ci-text">Creating this configuration stores it in the workspace. It does not deploy a cloud endpoint.</p>
    </Group>
    {draft.cloudModels.length > 0 && mode !== "edge-only" && <Group title="Cloud round trip" note={<NotReported />}>
      <p className="ci-text">Measured once a cloud endpoint is deployed, against {budget} in Routing &amp; safety.</p>
    </Group>}
  </>;
}

/* ---------- 05 Routing & safety ---------- */

const ROUTE_TEXT: Record<RouteMode, string> = {
  hybrid: "The edge planner picks each skill, the cloud policy streams action chunks, and an on-device policy covers link drops.",
  "cloud-only": "The cloud policy drives every motion. No on-device fallback.",
  "edge-only": "Planning and control run on the edge device. Nothing depends on the network.",
};
const ROUTES: readonly RouteMode[] = ["hybrid", "cloud-only", "edge-only"];
const OTHERWISE_OPTIONS = [{ value: "safe-hold", label: "Safe hold" }, { value: "pause-and-escalate", label: "Pause and escalate" }];
const SPEEDS = [0.25, 0.5, 0.6, 0.75, 1];
const speedLabel = (value: number) => `${fmtNumber(value, 2)}×`;

export function RoutingStep({ catalog, draft, update, errors, fixes, go }: StepProps) {
  const { mode, fallbackTrigger: trigger, fallbackAction: action, escalation, thermalGuard } = draft.routing;
  const onDevice = onDevicePolicies(draft);
  const blendTargets = [...new Set([action.blendInto?.trim() ?? "", ...onDevice.map(model => model.name)])].filter(Boolean);
  const speeds = [...new Set([...SPEEDS, ...(action.speedFactor !== null && Number.isFinite(action.speedFactor) ? [action.speedFactor] : [])])].toSorted((a, b) => a - b);
  const routeFixes = fixes("mode");
  return <>
    <ChoiceGroup legend="Route" type="radio" columns={3} errors={errors("mode")} onChange={value => update(current => setRouteMode(catalog, current, value as RouteMode))}
      options={ROUTES.map(route => ({ value: route, title: ROUTE_LABEL[route], text: ROUTE_TEXT[route], checked: mode === route }))}>
      {routeFixes.length > 0 && <span className="ci-fixes">{routeFixes.map(step => <button key={step} className="cfg-btn-text" type="button" onClick={() => go(step)}>{step === "edge" ? "Go to step 03 · Edge models" : "Go to step 04 · Cloud models"}</button>)}</span>}
    </ChoiceGroup>
    {mode === "hybrid" && <Group title="Fallback trigger" note="Any one condition hands control to the edge">
      <div className="ci-fields ci-fields--4">
        <NumberInput label="Cloud RTT p95 above, ms" value={trigger.cloudRttP95Ms} errors={errors("cloudRttP95Ms")} onChange={value => update(current => updateRouting(current, "fallbackTrigger", { cloudRttP95Ms: value }))} />
        <NumberInput label="Measured over, s" value={trigger.windowS} errors={errors("windowS")} onChange={value => update(current => updateRouting(current, "fallbackTrigger", { windowS: value }))} />
        <NumberInput label="Packet loss above, %" value={trigger.packetLossPct} max={100} errors={errors("packetLossPct")} onChange={value => update(current => updateRouting(current, "fallbackTrigger", { packetLossPct: value }))} />
        <NumberInput label="Chunk deadlines missed in a row" value={trigger.missedDeadlines} step={1} errors={errors("missedDeadlines")} onChange={value => update(current => updateRouting(current, "fallbackTrigger", { missedDeadlines: value }))} />
      </div>
      {errors("fallbackTrigger").length > 0 && <p className="ci-error">{errors("fallbackTrigger").join(" ")}</p>}
      <p className="portal-context-note">Leave a field empty to turn that condition off.</p>
    </Group>}
    <Group title={mode === "edge-only" ? "When a skill cannot run" : "Fallback action"} note={mode === "hybrid" ? "Valid actions of the current chunk finish first" : mode === "cloud-only" ? "No on-device fallback" : "Nothing depends on the network"}>
      <div className="ci-fields ci-fields--4">
        {mode !== "edge-only" && <NumberInput label="Action validity from observation, ms" value={action.validityMs} errors={errors("validityMs")} onChange={value => update(current => updateRouting(current, "fallbackAction", { validityMs: value }))} />}
        {mode === "hybrid" && <SelectInput label="Then blend into" value={action.blendInto?.trim() ?? ""} errors={errors("blendInto")} onChange={value => update(current => updateRouting(current, "fallbackAction", { blendInto: value || null }))}
          options={[...(action.blendInto?.trim() ? [] : [{ value: "", label: "Choose an on-device policy" }]), ...blendTargets.map(target => ({ value: target, label: target }))]} />}
        {mode === "hybrid" && <SelectInput label="At speed" value={action.speedFactor === null ? "" : String(action.speedFactor)} errors={errors("speedFactor")} onChange={value => update(current => updateRouting(current, "fallbackAction", { speedFactor: value ? Number(value) : null }))}
          options={[...(action.speedFactor === null ? [{ value: "", label: "Choose a speed" }] : []), ...speeds.map(speed => ({ value: String(speed), label: speedLabel(speed) }))]} />}
        <SelectInput label={mode === "hybrid" ? "If no edge skill" : mode === "cloud-only" ? "When the link degrades" : "If no skill can run"} value={action.otherwise} options={OTHERWISE_OPTIONS}
          onChange={value => update(current => updateRouting(current, "fallbackAction", { otherwise: value as typeof action.otherwise }))} />
      </div>
    </Group>
    <Group title="Escalation" note="Pause and ask a remote operator">
      <div className="ci-fields ci-fields--4">
        <NumberInput label="Progress stall above, s" value={escalation.stallS} errors={errors("stallS")} onChange={value => update(current => updateRouting(current, "escalation", { stallS: value }))} />
        <NumberInput label="Failed grasps in a row" value={escalation.failedGrasps} step={1} errors={errors("failedGrasps")} onChange={value => update(current => updateRouting(current, "escalation", { failedGrasps: value }))} />
        <SelectInput label="Verifier anomaly" value={escalation.onVerifierAnomaly} options={[{ value: "escalate", label: "Escalate" }, { value: "log", label: "Log only" }]}
          onChange={value => update(current => updateRouting(current, "escalation", { onVerifierAnomaly: value === "log" ? "log" : "escalate" }))} />
        <TextInput label="Operator channel" value={escalation.channel} errors={errors("channel")} onChange={channel => update(current => updateRouting(current, "escalation", { channel }))} />
      </div>
    </Group>
    <Group title="Thermal and power guard" note="Sheds edge load while active">
      <div className="ci-fields ci-fields--4">
        <NumberInput label="SoC temperature at or above, °C" value={thermalGuard.socTempC} errors={errors("socTempC")} onChange={value => update(current => updateRouting(current, "thermalGuard", { socTempC: value }))} />
        <NumberInput label="Over-current events per 10 min" value={thermalGuard.overCurrentEventsPer10Min} step={1} errors={errors("overCurrentEventsPer10Min")} onChange={value => update(current => updateRouting(current, "thermalGuard", { overCurrentEventsPer10Min: value }))} />
        <TextInput label="Guard action" span="two" value={thermalGuard.action} errors={errors("thermalAction")} onChange={text => update(current => updateRouting(current, "thermalGuard", { action: text }))} />
      </div>
    </Group>
    <Group title="Safety envelope" note={`${draft.safety.name} · carried over from the starting configuration`}>
      <DataTable<SafetyDefinition> label="Safety envelope" caption={draft.safety.note ? `${safetyRange(draft.safety)} · ${draft.safety.note}` : safetyRange(draft.safety)} rows={draft.safety.definitions} rowKey={item => item.id}
        empty="No safety checks are defined." columns={[
          { key: "check", header: "Check", cell: item => `${item.id} · ${item.name}` },
          { key: "rule", header: "Threshold", wrap: true, cell: item => item.rule },
          { key: "severity", header: "Severity", wrap: true, cell: item => SEVERITY_LABEL[item.severity], detail: item => item.criticalWhen ?? null },
        ]} />
    </Group>
  </>;
}
/* ---------- 06 Review: one panel per step with Edit ---------- */

function EditButton({ step, label, go }: { step: DraftStep; label: string; go: (step: DraftStep) => void }) {
  return <button className="cfg-btn-text" type="button" aria-label={`Edit ${label}`} onClick={() => go(step)}>Edit</button>;
}
const withBadge = (text: ReactNode, provenance: Provenance | null, now: number | null) => <>{text}<span className="ci-dd-prov"><ProvenanceBadge provenance={provenance} now={now} /></span></>;

export function ReviewPanels({ workspace, draft, go, now, connected, live, rev }: Pick<StepProps, "workspace" | "draft" | "go" | "now"> & { connected: readonly Robot[]; live: Readonly<Record<string, LiveBinding | undefined>>; rev: string }) {
  const base = getConfiguration(workspace, draft.baseId);
  const robot = draft.robot, hardware = draft.edgeHardware, mode = selectedPowerMode(hardware);
  const memory = residentMemory(draft);
  const device = connected[0] ?? null;
  const binding = device ? live[device.id] : undefined;
  const routing = draft.routing;
  const critical = draft.safety.definitions.filter(item => item.severity === "critical").length;
  const edgeFacts: FactItem[] = draft.edgeModels.length
    ? draft.edgeModels.map(model => ({ label: MODEL_ROLE_LABEL[model.role], value: model.name, detail: MODEL_STATE_LABEL[model.state] }))
    : [{ label: "Models", value: "None selected" }];
  const cloudFacts: FactItem[] = routing.mode === "edge-only" || !draft.cloudModels.length
    ? [{ label: "Models", value: routing.mode === "edge-only" ? "None (edge only)" : "None selected" }]
    : [...draft.cloudModels.map(model => ({ label: MODEL_ROLE_LABEL[model.role], value: model.name, detail: [model.serving, model.chunk ? `${fmtNumber(model.chunk.actions)} actions at ${fmtNumber(model.chunk.rateHz)} Hz per chunk` : null].filter(Boolean).join(" · ") || undefined })),
      { label: "Round trip", value: <NotReported>Not measured · after the first deploy</NotReported> }];
  return <div className="portal-two-column ci-review">
    <Panel eyebrow="Step 01" title="Robot" titleId="ci-review-robot" action={<EditButton step="robot" label="robot" go={go} />}>
      <FactList facts={[
        { label: "Saved as", value: draft.saveAs === "revision" && base ? `${rev} of ${draft.name.trim() || base.name}` : `New configuration · ${rev}` },
        { label: "Robot", value: robot.name, detail: robot.summary },
        { label: "Sim twin", value: robot.simTwin ? `${robot.simTwin.name} · ${robot.simTwin.engine}` : "None" },
        { label: "Control", value: withBadge(`${robot.controlRateHz === null ? "Rate not reported" : `${fmtNumber(robot.controlRateHz)} Hz`} · ${robot.actionSpace}`, robot.provenance, now) },
      ]} />
    </Panel>
    <Panel eyebrow="Step 02" title="Edge hardware" titleId="ci-review-hardware" action={<EditButton step="hardware" label="edge hardware" go={go} />}>
      <FactList facts={[
        { label: "Device", value: hardware.name },
        { label: "Power mode", value: mode ? `${mode.label}${mode.recommended ? " (recommended)" : ""}` : <NotReported /> },
        { label: "Memory", value: `${fmtFixed(hardware.memoryGiB)} GiB usable, shared` },
        { label: "Connected", value: device ? withBadge(device.name, binding?.data?.provenance ?? null, now) : "No device on this hardware" },
      ]} />
    </Panel>
    <Panel eyebrow="Step 03" title="Edge models" titleId="ci-review-edge" action={<EditButton step="edge" label="edge models" go={go} />}>
      <FactList facts={[...edgeFacts, { label: "Memory", value: memory.total === null ? <NotReported /> : withBadge(`${fmtFixed(memory.total)} of ${fmtFixed(hardware.memoryGiB)} GiB`, ESTIMATE, now) }]} />
    </Panel>
    <Panel eyebrow="Step 04" title="Cloud models" titleId="ci-review-cloud" action={<EditButton step="cloud" label="cloud models" go={go} />}>
      <FactList facts={cloudFacts} />
    </Panel>
    <Panel eyebrow="Step 05" title="Routing & safety" titleId="ci-review-routing" className="ci-review__wide" action={<EditButton step="routing" label="routing and safety" go={go} />}>
      <FactList className="ci-facts-2" facts={[
        { label: "Route", value: ROUTE_LABEL[routing.mode], detail: routing.defaultRoute },
        ...(routing.mode === "hybrid" ? [{ label: "Fallback trigger", value: describeTrigger(routing) }] : []),
        { label: routing.mode === "edge-only" ? "If a skill cannot run" : "Fallback action", value: describeAction(routing) },
        { label: "Escalation", value: describeEscalation(routing) },
        { label: "Thermal guard", value: describeThermal(routing) },
        { label: "Safety envelope", value: `${draft.safety.name} · ${safetyRange(draft.safety)}`, detail: critical ? `${critical} critical ${critical === 1 ? "check" : "checks"}` : undefined },
      ]} />
    </Panel>
  </div>;
}
