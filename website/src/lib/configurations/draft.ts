/**
 * The New configuration form (`/app/configurations/new`). A draft starts from an
 * existing configuration's current revision, is edited with parts the workspace
 * already describes (robots, edge hardware, edge and cloud models and routing
 * found in its revisions), is checked step by step, and is saved either as a new
 * configuration (`createConfiguration`) or as the next revision of the
 * configuration it started from (`addRevision`).
 *
 * The compatibility check never invents evidence. For each check it reuses the
 * stored check of a revision with the same premise on the same edge hardware (the
 * workspace's evidence for that combination); otherwise it computes what declared
 * values and recorded entries show; otherwise it says "Not measured". Measured
 * values are never stored, so live device data is not used here.
 */
import { fmtCount, fmtFixed, fmtNumber, fmtPct } from "./format";
import { createConfiguration, uniqueId } from "./mutations";
import { currentRevision, getConfiguration, getRevision, getSuite, listConfigurations, nextRevision, robotsFor } from "./selectors";
import type {
  ActivityEvent, CheckVerdict, CloudModel, CompatibilityCheck, ConfigRevision, Configuration, ConvoyWorkspace, EdgeHardware, EdgeModel, FlagRules,
  ModelRole, ModelState, Robot, RobotSpec, RouteMode, RoutingPolicy, RunStatus, SafetyEnvelope, StoredProvenance,
} from "./types";

/* ---------- steps and labels ---------- */

export type DraftStep = "robot" | "hardware" | "edge" | "cloud" | "routing" | "review";
export const DRAFT_STEPS: ReadonlyArray<{ id: DraftStep; label: string; title: string }> = [
  { id: "robot", label: "Robot", title: "Robot" },
  { id: "hardware", label: "Edge hardware", title: "Edge hardware" },
  { id: "edge", label: "Edge model", title: "Edge models" },
  { id: "cloud", label: "Cloud model", title: "Cloud models" },
  { id: "routing", label: "Routing & safety", title: "Routing & safety" },
  { id: "review", label: "Review", title: "Review" },
];
/** A `?step=` value, or the first step. */
export function parseStep(value: string | null | undefined): DraftStep {
  return DRAFT_STEPS.find(step => step.id === value)?.id ?? "robot";
}
/** "01" for the first step. */
export const stepNumber = (step: DraftStep) => String(DRAFT_STEPS.findIndex(item => item.id === step) + 1).padStart(2, "0");

export const MODEL_ROLE_LABEL: Record<ModelRole, string> = { planner: "Planner", policy: "Policy", "fallback-policy": "Fallback policy", "skill-pack": "Skill pack", verifier: "Verifier" };
export const MODEL_STATE_LABEL: Record<ModelState, string> = { active: "Active", "fallback-only": "Fallback only", proposed: "Proposed", blocked: "Blocked", "not-deployed": "Not deployed" };
export const ROUTE_LABEL: Record<RouteMode, string> = { hybrid: "Edge plans, cloud acts", "cloud-only": "Cloud only", "edge-only": "Edge only" };
/** Resident-memory figures are each model's declared estimate: illustrative, not measured. */
export const ESTIMATE: StoredProvenance = { kind: "sample", source: "Resident-memory estimates declared for each model" };
const NOT_REPORTED: StoredProvenance = { kind: "not-reported" };

/* ---------- draft and catalog ---------- */

export type SaveAs = "configuration" | "revision";
type SimTwin = NonNullable<RobotSpec["simTwin"]>;

export interface ConfigurationDraft {
  /** Configuration whose current revision the draft started from. */
  baseId: string;
  baseRev: string;
  /** A new configuration (revision r1), or the next revision of `baseId`. */
  saveAs: SaveAs;
  name: string;
  purpose: string;
  /** Revision note, e.g. what changed. */
  note: string;
  robot: RobotSpec;
  edgeHardware: EdgeHardware;
  edgeModels: EdgeModel[];
  cloudModels: CloudModel[];
  routing: RoutingPolicy;
  safety: SafetyEnvelope;
  flagRules: FlagRules;
  suiteId: string | null;
}

export interface CatalogItem<T> { key: string; value: T; /** Edge hardware of the newest revision that declares it. */ hardware: string }
/** Every part the workspace's revisions declare, newest definition first, each once. */
export interface DraftCatalog {
  robots: Array<CatalogItem<RobotSpec>>;
  /** Simulation twins seen with each robot. */
  twins: Array<CatalogItem<SimTwin> & { robot: string }>;
  /** Edge hardware with the flag rules of the newest revision that uses it. */
  hardware: Array<CatalogItem<EdgeHardware> & { flagRules: FlagRules }>;
  edgeModels: Array<CatalogItem<EdgeModel>>;
  cloudModels: Array<CatalogItem<CloudModel>>;
  /** Routing of every revision, keyed by route mode. */
  routing: Array<CatalogItem<RoutingPolicy>>;
}

/** A model in a role, e.g. "fallback-policy:SmolVLA (450M)": the same model in another role is another choice. */
export const modelKey = (model: Pick<EdgeModel | CloudModel, "role" | "name">) => `${model.role}:${model.name}`;
export const twinKey = (twin: SimTwin) => `${twin.name} · ${twin.engine}`;
const ROLE_ORDER: Record<ModelRole, number> = { planner: 0, policy: 1, "fallback-policy": 2, "skill-pack": 3, verifier: 4 };
const byRole = <T extends { role: ModelRole }>(a: T, b: T) => ROLE_ORDER[a.role] - ROLE_ORDER[b.role];
const isMotor = (model: EdgeModel) => model.role === "policy" || model.role === "fallback-policy";
const finite = (value: number | null | undefined): value is number => typeof value === "number" && Number.isFinite(value);
const round2 = (value: number) => Math.round(value * 100) / 100;
const gib = (value: number) => fmtFixed(value, 1);

function revisionsNewestFirst(ws: ConvoyWorkspace): ConfigRevision[] {
  return ws.configurations.flatMap(config => config.revisions).toSorted((a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt));
}

export function buildCatalog(ws: ConvoyWorkspace): DraftCatalog {
  const catalog: DraftCatalog = { robots: [], twins: [], hardware: [], edgeModels: [], cloudModels: [], routing: [] };
  const add = <T>(list: Array<CatalogItem<T>>, key: string, value: T, hardware: string) => { if (!list.some(item => item.key === key)) list.push({ key, value, hardware }); };
  for (const revision of revisionsNewestFirst(ws)) {
    const hardware = revision.edgeHardware.name;
    add(catalog.robots, revision.robot.name, revision.robot, hardware);
    const twin = revision.robot.simTwin;
    if (twin && !catalog.twins.some(item => item.key === twinKey(twin) && item.robot === revision.robot.name)) catalog.twins.push({ key: twinKey(twin), value: twin, hardware, robot: revision.robot.name });
    if (!catalog.hardware.some(item => item.key === hardware)) catalog.hardware.push({ key: hardware, value: revision.edgeHardware, hardware, flagRules: revision.flagRules });
    for (const model of revision.edgeModels) add(catalog.edgeModels, modelKey(model), model, hardware);
    for (const model of revision.cloudModels) add(catalog.cloudModels, modelKey(model), model, hardware);
    catalog.routing.push({ key: revision.routing.mode, value: revision.routing, hardware });
  }
  catalog.edgeModels.sort((a, b) => byRole(a.value, b.value));
  catalog.cloudModels.sort((a, b) => byRole(a.value, b.value));
  return catalog;
}

/** A draft holding a configuration's current revision. A revision keeps the configuration's name; a new configuration asks for one. */
export function draftFromConfiguration(config: Configuration, saveAs: SaveAs, keep: Partial<Pick<ConfigurationDraft, "name" | "purpose" | "note">> = {}): ConfigurationDraft {
  const revision = currentRevision(config);
  return {
    baseId: config.id, baseRev: revision.rev, saveAs,
    name: keep.name ?? (saveAs === "revision" ? config.name : ""), purpose: keep.purpose ?? config.purpose, note: keep.note ?? "",
    robot: revision.robot, edgeHardware: revision.edgeHardware, edgeModels: revision.edgeModels, cloudModels: revision.cloudModels,
    routing: revision.routing, safety: revision.safety, flagRules: revision.flagRules, suiteId: config.suiteId,
  };
}
/** The configuration a new one starts from by default: the recommended one, else the most recently updated. */
export function defaultTemplate(ws: ConvoyWorkspace): Configuration | null {
  return ws.configurations.find(config => config.recommended) ?? listConfigurations(ws)[0] ?? null;
}
/**
 * Where the form starts: `from` names a configuration to revise; otherwise the
 * default template is copied into a new configuration. `missing` echoes a `from`
 * that is not in the workspace; null when the workspace has no configuration.
 */
export function initialDraft(ws: ConvoyWorkspace, from?: string | null): { draft: ConfigurationDraft; missing: string | null } | null {
  const base = from ? getConfiguration(ws, from) : null;
  if (base) return { draft: draftFromConfiguration(base, "revision"), missing: null };
  const template = defaultTemplate(ws);
  return template ? { draft: draftFromConfiguration(template, "configuration"), missing: from || null } : null;
}

/* ---------- edits (each returns a new draft) ---------- */

/** Starts over from another configuration: steps 02–05 take its values; a typed name, note and edited purpose stay for a new configuration. */
export function startFrom(ws: ConvoyWorkspace, draft: ConfigurationDraft, configId: string): ConfigurationDraft {
  const config = getConfiguration(ws, configId);
  if (!config || config.id === draft.baseId) return draft;
  if (draft.saveAs === "revision") return draftFromConfiguration(config, "revision", { note: draft.note });
  const previous = getConfiguration(ws, draft.baseId);
  const editedPurpose = !previous || draft.purpose !== previous.purpose;
  return draftFromConfiguration(config, "configuration", { name: draft.name, note: draft.note, ...(editedPurpose ? { purpose: draft.purpose } : {}) });
}
export function setSaveAs(ws: ConvoyWorkspace, draft: ConfigurationDraft, saveAs: SaveAs): ConfigurationDraft {
  if (saveAs === draft.saveAs) return draft;
  const base = getConfiguration(ws, draft.baseId);
  if (saveAs === "revision") return base ? { ...draft, saveAs, name: base.name } : draft;
  return { ...draft, saveAs, name: base && draft.name.trim() === base.name ? "" : draft.name };
}
export function setRobot(catalog: DraftCatalog, draft: ConfigurationDraft, name: string): ConfigurationDraft {
  const item = catalog.robots.find(entry => entry.key === name);
  return item ? { ...draft, robot: item.value } : draft;
}
export function setTwin(catalog: DraftCatalog, draft: ConfigurationDraft, key: string | null): ConfigurationDraft {
  if (key === null) return { ...draft, robot: { ...draft.robot, simTwin: null } };
  const item = catalog.twins.find(entry => entry.key === key && entry.robot === draft.robot.name);
  return item ? { ...draft, robot: { ...draft.robot, simTwin: item.value } } : draft;
}
/** Another device brings its own power modes, selected mode and flag rules. */
export function setHardware(catalog: DraftCatalog, draft: ConfigurationDraft, name: string): ConfigurationDraft {
  const item = catalog.hardware.find(entry => entry.key === name);
  return item && name !== draft.edgeHardware.name ? { ...draft, edgeHardware: item.value, flagRules: item.flagRules } : draft;
}
export function setPowerMode(draft: ConfigurationDraft, modeId: string): ConfigurationDraft {
  return draft.edgeHardware.powerModes.some(mode => mode.id === modeId) ? { ...draft, edgeHardware: { ...draft.edgeHardware, powerModeId: modeId } } : draft;
}
export function toggleEdgeModel(catalog: DraftCatalog, draft: ConfigurationDraft, key: string, on: boolean): ConfigurationDraft {
  const rest = draft.edgeModels.filter(model => modelKey(model) !== key);
  if (!on) return { ...draft, edgeModels: rest };
  const item = catalog.edgeModels.find(entry => entry.key === key);
  return item ? { ...draft, edgeModels: [...rest, item.value].toSorted(byRole) } : draft;
}
/** Sets the cloud model for one role (policy, verifier, …); null removes it. */
export function setCloudModel(catalog: DraftCatalog, draft: ConfigurationDraft, role: ModelRole, key: string | null): ConfigurationDraft {
  const rest = draft.cloudModels.filter(model => model.role !== role);
  const item = key ? catalog.cloudModels.find(entry => entry.key === key && entry.value.role === role) : null;
  if (key && !item) return draft;
  return { ...draft, cloudModels: (item ? [...rest, item.value] : rest).toSorted(byRole) };
}
export function toggleCloudModel(catalog: DraftCatalog, draft: ConfigurationDraft, key: string, on: boolean): ConfigurationDraft {
  const rest = draft.cloudModels.filter(model => modelKey(model) !== key);
  if (!on) return { ...draft, cloudModels: rest };
  const item = catalog.cloudModels.find(entry => entry.key === key);
  return item ? { ...draft, cloudModels: [...rest, item.value].toSorted(byRole) } : draft;
}

const GENERIC_ROUTE: Record<RouteMode, Pick<RoutingPolicy, "summary" | "defaultRoute">> = {
  hybrid: { summary: "Edge plans, cloud acts", defaultRoute: "The edge planner picks each skill, the cloud policy streams action chunks, and an on-device policy takes over while the link is degraded." },
  "cloud-only": { summary: "Cloud acts, no edge fallback", defaultRoute: "The edge planner picks each skill and executes the cloud policy’s action chunks." },
  "edge-only": { summary: "Everything on the edge", defaultRoute: "The edge planner and the on-device policy run every skill on the edge device." },
};
/**
 * Switches the route. Its summary, fallback trigger and fallback action come from
 * the newest revision with that route (on the same hardware first); without one
 * they start empty. Escalation and the thermal guard stay as edited.
 */
export function setRouteMode(catalog: DraftCatalog, draft: ConfigurationDraft, mode: RouteMode): ConfigurationDraft {
  if (draft.routing.mode === mode) return draft;
  const template = (catalog.routing.find(item => item.key === mode && item.hardware === draft.edgeHardware.name) ?? catalog.routing.find(item => item.key === mode))?.value;
  const shape: Pick<RoutingPolicy, "summary" | "defaultRoute" | "fallbackTrigger" | "fallbackAction"> = template ?? {
    ...GENERIC_ROUTE[mode],
    fallbackTrigger: { cloudRttP95Ms: null, windowS: null, packetLossPct: null, missedDeadlines: null },
    fallbackAction: { validityMs: mode === "edge-only" ? null : draft.routing.fallbackAction.validityMs, blendInto: null, speedFactor: null, otherwise: mode === "cloud-only" ? "pause-and-escalate" : "safe-hold" },
  };
  return { ...draft, routing: { ...draft.routing, mode, summary: shape.summary, defaultRoute: shape.defaultRoute, fallbackTrigger: shape.fallbackTrigger, fallbackAction: shape.fallbackAction } };
}
type RoutingPart = "fallbackTrigger" | "fallbackAction" | "escalation" | "thermalGuard";
export function updateRouting<K extends RoutingPart>(draft: ConfigurationDraft, part: K, patch: Partial<RoutingPolicy[K]>): ConfigurationDraft {
  return { ...draft, routing: { ...draft.routing, [part]: { ...draft.routing[part], ...patch } } };
}

/* ---------- derived facts ---------- */

/** Declared resident memory of the selected edge models; `total` is null while any estimate is missing. */
export function residentMemory(draft: Pick<ConfigurationDraft, "edgeModels">): { total: number | null; known: number; missing: EdgeModel[] } {
  const known = round2(draft.edgeModels.reduce((sum, model) => sum + (model.residentGiB ?? 0), 0));
  const missing = draft.edgeModels.filter(model => model.residentGiB === null);
  return { total: missing.length ? null : known, known, missing };
}
export const selectedPowerMode = (hardware: EdgeHardware) => hardware.powerModes.find(mode => mode.id === hardware.powerModeId) ?? null;
/** On-device policies that can take over (or drive every motion). */
export const onDevicePolicies = (draft: Pick<ConfigurationDraft, "edgeModels">) => draft.edgeModels.filter(isMotor);
const firstWord = (text: string) => text.trim().split(/\s+/)[0]?.toLowerCase() ?? "";
/** Whether a free-text "blend into" target names a model (e.g. "SmolVLA on the Jetson" names "SmolVLA (450M)"). */
export function namesModel(target: string, model: Pick<EdgeModel, "name" | "shortName">): boolean {
  const text = target.trim().toLowerCase();
  return !!text && (text === model.name.toLowerCase() || text === model.shortName.toLowerCase() || (!!firstWord(model.name) && text.startsWith(firstWord(model.name))));
}
/** Robots with a live device binding on this edge hardware (by the revision they run). */
export function connectedRobots(ws: ConvoyWorkspace, hardwareName: string): Robot[] {
  return ws.robots.filter(robot => {
    if (!robot.deviceId || !robot.configId) return false;
    const config = getConfiguration(ws, robot.configId);
    return (config ? getRevision(config, robot.rev) : null)?.edgeHardware.name === hardwareName;
  }).toSorted((a, b) => a.name.localeCompare(b.name));
}
/** "S1–S6", "S1" or "No safety checks". */
export function safetyRange(safety: SafetyEnvelope): string {
  const ids = safety.definitions.map(item => item.id);
  return ids.length > 1 ? `${ids[0]}–${ids[ids.length - 1]}` : ids[0] ?? "No safety checks";
}
const names = (models: ReadonlyArray<{ shortName: string }>) => models.map(model => model.shortName).join(" · ");

/** The short value under a step in the stepper. */
export function stepValue(draft: ConfigurationDraft, step: DraftStep, checks?: readonly CompatibilityCheck[]): string {
  switch (step) {
    case "robot": return [draft.robot.name, draft.robot.simTwin?.name].filter(Boolean).join(" · ");
    case "hardware": return [draft.edgeHardware.name, selectedPowerMode(draft.edgeHardware)?.label].filter(Boolean).join(" · ");
    case "edge": return names(draft.edgeModels) || "None selected";
    case "cloud": return draft.routing.mode === "edge-only" && !draft.cloudModels.length ? "None (edge only)" : names(draft.cloudModels) || "None selected";
    case "routing": {
      const rtt = draft.routing.fallbackTrigger.cloudRttP95Ms;
      const route = draft.routing.mode === "hybrid" && rtt !== null ? `Fallback at RTT p95 > ${fmtNumber(rtt, 0)} ms` : ROUTE_LABEL[draft.routing.mode];
      return `${route} · ${safetyRange(draft.safety)}`;
    }
    case "review": return checks ? checkSummary(checks) : "Compatibility check, then create";
  }
}

/* ---------- routing in words (review) ---------- */

const OTHERWISE_LABEL: Record<RoutingPolicy["fallbackAction"]["otherwise"], string> = { "safe-hold": "safe hold", "pause-and-escalate": "pause and escalate" };
/** "RTT p95 > 350 ms over 3 s · loss > 5 % · 3 missed deadlines", or "None". */
export function describeTrigger(routing: RoutingPolicy): string {
  const { cloudRttP95Ms: rtt, windowS, packetLossPct: loss, missedDeadlines: missed } = routing.fallbackTrigger;
  const parts = [
    rtt !== null && `RTT p95 > ${fmtNumber(rtt, 0)} ms${windowS !== null ? ` over ${fmtNumber(windowS)} s` : ""}`,
    loss !== null && `loss > ${fmtNumber(loss)} %`,
    missed !== null && `${fmtCount(missed)} missed ${missed === 1 ? "deadline" : "deadlines"}`,
  ].filter(Boolean);
  return parts.join(" · ") || "None";
}
/** "SmolVLA on the Jetson at 0.6× · else safe hold"; cloud only: "Safe hold … when the link degrades". */
export function describeAction(routing: RoutingPolicy): string {
  const { blendInto, speedFactor, otherwise, validityMs } = routing.fallbackAction;
  const validity = validityMs !== null ? `actions valid ${fmtNumber(validityMs, 0)} ms` : null;
  if (routing.mode === "hybrid") return [`${blendInto?.trim() || "No on-device policy"}${finite(speedFactor) ? ` at ${fmtNumber(speedFactor, 2)}×` : ""}`, `else ${OTHERWISE_LABEL[otherwise]}`, validity].filter(Boolean).join(" · ");
  if (routing.mode === "cloud-only") return [`${OTHERWISE_LABEL[otherwise]} when the link degrades`, validity].filter(Boolean).join(" · ").replace(/^./, letter => letter.toUpperCase());
  return `${OTHERWISE_LABEL[otherwise]} when no skill can run`.replace(/^./, letter => letter.toUpperCase());
}
/** "Stall > 10 s · 2 failed grasps · verifier anomaly: escalate · Operator console". */
export function describeEscalation(routing: RoutingPolicy): string {
  const { stallS, failedGrasps, onVerifierAnomaly, channel } = routing.escalation;
  return [stallS !== null && `Stall > ${fmtNumber(stallS)} s`, failedGrasps !== null && `${fmtCount(failedGrasps)} failed ${failedGrasps === 1 ? "grasp" : "grasps"}`,
    `verifier anomaly: ${onVerifierAnomaly === "escalate" ? "escalate" : "log only"}`, channel.trim()].filter(Boolean).join(" · ");
}
/** "SoC ≥ 90 °C or ≥ 4 over-current events / 10 min · Unload the fallback policy …". */
export function describeThermal(routing: RoutingPolicy): string {
  const { socTempC, overCurrentEventsPer10Min: events, action } = routing.thermalGuard;
  const when = [socTempC !== null && `SoC ≥ ${fmtNumber(socTempC)} °C`, events !== null && `≥ ${fmtCount(events)} over-current events / 10 min`].filter(Boolean).join(" or ");
  return [when || "No trigger set", action.trim()].filter(Boolean).join(" · ");
}

/* ---------- validation ---------- */

/** `step` holds the field; `fix` is where the problem is resolved when that is another step (e.g. a route that needs a cloud policy). */
export interface DraftIssue { step: DraftStep; field: string; message: string; fix?: DraftStep }

/** Everything that must change before the draft can be saved, in step order. */
export function draftIssues(ws: ConvoyWorkspace, draft: ConfigurationDraft): DraftIssue[] {
  const issues: DraftIssue[] = [];
  const add = (step: DraftStep, field: string, message: string, fix?: DraftStep) => issues.push({ step, field, message, ...(fix ? { fix } : {}) });
  const name = draft.name.trim();
  const base = getConfiguration(ws, draft.baseId);
  if (draft.saveAs === "revision" && !base) add("robot", "saveAs", "The configuration this revision belongs to is no longer in the workspace. Save it as a new configuration.");
  if (!name) add("robot", "name", "Name the configuration.");
  else if (ws.configurations.some(config => config.name.trim().toLowerCase() === name.toLowerCase() && !(draft.saveAs === "revision" && config.id === draft.baseId))) add("robot", "name", `Another configuration is already called “${name}”.`);

  const memory = residentMemory(draft);
  if (memory.known > draft.edgeHardware.memoryGiB) add("edge", "edgeModels", `The selected models need ${gib(memory.known)} GiB; the ${draft.edgeHardware.name} has ${gib(draft.edgeHardware.memoryGiB)} GiB usable.`);

  const { mode, fallbackTrigger: trigger, fallbackAction: action, escalation, thermalGuard } = draft.routing;
  const cloudPolicy = draft.cloudModels.some(model => model.role === "policy");
  const onDevice = onDevicePolicies(draft);
  if (mode !== "edge-only" && !cloudPolicy) add("routing", "mode", `${ROUTE_LABEL[mode]} needs a cloud motor policy: choose one in step 04, or pick another route.`, "cloud");
  if (mode === "hybrid" && !onDevice.length) add("routing", "mode", "Edge plans, cloud acts needs an on-device policy for link drops: choose one in step 03, or pick another route.", "edge");
  if (mode === "edge-only" && !draft.edgeModels.some(model => model.role === "policy")) add("routing", "mode", "Edge only needs an on-device motor policy: choose one in step 03.", "edge");
  if (mode === "edge-only" && draft.cloudModels.length) add("routing", "mode", "Edge only uses no cloud models: set them to None in step 04.", "cloud");

  const number = (field: string, label: string, value: number | null, rule: { integer?: boolean; max?: number; positive?: boolean } = {}) => {
    if (value === null) return;
    if (!Number.isFinite(value) || value < 0 || (rule.positive && value === 0)) add("routing", field, `${label} must be ${rule.positive ? "more than 0" : "0 or more"}.`);
    else if (rule.integer && !Number.isInteger(value)) add("routing", field, `${label} must be a whole number.`);
    else if (rule.max !== undefined && value > rule.max) add("routing", field, `${label} must be at most ${rule.max}.`);
  };
  if (mode === "hybrid") {
    if (trigger.cloudRttP95Ms === null && trigger.packetLossPct === null && trigger.missedDeadlines === null) add("routing", "fallbackTrigger", "Set at least one condition that hands control to the edge.");
    number("cloudRttP95Ms", "Cloud RTT p95", trigger.cloudRttP95Ms, { positive: true });
    number("windowS", "The measurement window", trigger.windowS, { positive: true });
    number("packetLossPct", "Packet loss", trigger.packetLossPct, { max: 100 });
    number("missedDeadlines", "Missed deadlines", trigger.missedDeadlines, { integer: true, positive: true });
    const target = action.blendInto?.trim() ?? "";
    if (!target) add("routing", "blendInto", "Choose the on-device policy that takes over.");
    else if (onDevice.length && !onDevice.some(model => namesModel(target, model))) add("routing", "blendInto", `“${target}” is not one of the on-device policies selected in step 03.`, "edge");
    if (action.speedFactor === null || !Number.isFinite(action.speedFactor) || action.speedFactor <= 0 || action.speedFactor > 1) add("routing", "speedFactor", "Choose a fallback speed above 0× and at most 1×.");
  }
  if (mode !== "edge-only") {
    if (action.validityMs === null) add("routing", "validityMs", "Enter how long an action stays valid after its observation.");
    else number("validityMs", "Action validity", action.validityMs, { positive: true });
  }
  number("stallS", "Progress stall", escalation.stallS, { positive: true });
  number("failedGrasps", "Failed grasps", escalation.failedGrasps, { integer: true, positive: true });
  if (!escalation.channel.trim()) add("routing", "channel", "Name the operator channel.");
  if (thermalGuard.socTempC !== null && !Number.isFinite(thermalGuard.socTempC)) add("routing", "socTempC", "SoC temperature must be a number.");
  number("overCurrentEventsPer10Min", "Over-current events", thermalGuard.overCurrentEventsPer10Min, { integer: true, positive: true });
  if (!thermalGuard.action.trim()) add("routing", "thermalAction", "Describe what the guard does.");
  return issues;
}

/* ---------- compatibility check ---------- */

type Stack = Pick<ConfigRevision, "robot" | "edgeHardware" | "edgeModels" | "cloudModels" | "routing">;
/** Checks the draft computes; stored checks with other ids carry over from the starting revision as device evidence. */
export const COMPUTED_CHECKS = ["planner-fit", "policy-rate", "cloud-rtt", "power", "memory"] as const;
type ComputedId = typeof COMPUTED_CHECKS[number];
const isComputed = (id: string): id is ComputedId => (COMPUTED_CHECKS as readonly string[]).includes(id);
const keys = (models: ReadonlyArray<EdgeModel | CloudModel>) => models.map(modelKey).toSorted().join(",");

/** What a check depends on: a stored check applies to a draft only when this matches. */
function premise(id: ComputedId, stack: Stack): string {
  const hardware = stack.edgeHardware.name;
  switch (id) {
    case "planner-fit": return [hardware, keys(stack.edgeModels.filter(model => model.role === "planner"))].join("|");
    case "memory": return [hardware, stack.edgeHardware.memoryGiB, stack.edgeModels.map(model => `${modelKey(model)}=${model.residentGiB}`).toSorted().join(",")].join("|");
    case "policy-rate": return [hardware, stack.routing.mode, stack.robot.controlRateHz, keys(stack.edgeModels.filter(isMotor)),
      stack.cloudModels.filter(model => model.role === "policy").map(model => `${modelKey(model)}@${model.chunk?.rateHz ?? ""}`).toSorted().join(",")].join("|");
    case "power": { const mode = selectedPowerMode(stack.edgeHardware); return [hardware, mode?.id, mode?.capW].join("|"); }
    case "cloud-rtt": return [hardware, stack.routing.mode, keys(stack.cloudModels), stack.routing.fallbackTrigger.cloudRttP95Ms, stack.routing.fallbackAction.validityMs].join("|");
  }
}

/** Stored checks of revisions on the draft's edge hardware: the starting revision first, then newest first. */
function storedChecks(ws: ConvoyWorkspace, draft: ConfigurationDraft): Array<{ check: CompatibilityCheck; stack: ConfigRevision; base: boolean }> {
  const config = getConfiguration(ws, draft.baseId);
  const base = config ? getRevision(config, draft.baseRev) : null;
  const revisions = [...(base ? [base] : []), ...revisionsNewestFirst(ws).filter(revision => revision !== base)].filter(revision => revision.edgeHardware.name === draft.edgeHardware.name);
  return revisions.flatMap(revision => revision.compatibility.map(check => ({ check, stack: revision, base: revision === base })));
}

/** The same model stored on a revision with this edge hardware, carrying evidence. */
function modelOnHardware(ws: ConvoyWorkspace, hardware: string, model: EdgeModel): EdgeModel | null {
  for (const revision of revisionsNewestFirst(ws)) {
    if (revision.edgeHardware.name !== hardware) continue;
    const found = revision.edgeModels.find(item => modelKey(item) === modelKey(model) && item.evidence);
    if (found) return found;
  }
  return null;
}

const STATUS_TEXT: Record<RunStatus, string> = { queued: "queued", running: "running", "passed-gate": "passed the gate", "below-gate": "below the gate", completed: "completed", "did-not-qualify": "did not qualify", cancelled: "cancelled", failed: "failed" };

function plannerCheck(ws: ConvoyWorkspace, draft: ConfigurationDraft): CompatibilityCheck | null {
  const planner = draft.edgeModels.find(model => model.role === "planner");
  if (!planner) return null;
  const hardware = draft.edgeHardware.name;
  const known = modelOnHardware(ws, hardware, planner);
  const evidence = known?.evidence?.kind === "recorded" ? known.evidence : null;
  if (known && evidence && known.state === "active") {
    const context = finite(planner.contextTokens) ? `, ${fmtCount(planner.contextTokens)}-token context` : "";
    return { id: "planner-fit", verdict: "pass", title: "Planner model loads and runs on this device", detail: `${planner.name} on ${planner.runtime}${context}.`, evidence };
  }
  if (known && evidence && known.state === "blocked") return { id: "planner-fit", verdict: "block", title: "Planner model on this device", detail: `${planner.name}: ${known.detail ?? `blocked on the ${hardware}`}.`, evidence };
  return { id: "planner-fit", verdict: "pending", title: "Planner model on this device", detail: `No recorded run shows ${planner.name} loading on the ${hardware}.`, evidence: NOT_REPORTED };
}

function policyRateCheck(ws: ConvoyWorkspace, draft: ConfigurationDraft): CompatibilityCheck | null {
  const hardware = draft.edgeHardware.name, mode = draft.routing.mode, rate = draft.robot.controlRateHz;
  const onDevice = onDevicePolicies(draft);
  if (mode !== "cloud-only" && onDevice.length) {
    const model = onDevice.find(item => item.role === (mode === "edge-only" ? "policy" : "fallback-policy")) ?? onDevice[0];
    const title = mode === "edge-only" ? "On-device policy at the control rate" : "On-device fallback policy at the control rate";
    const known = modelOnHardware(ws, hardware, model);
    const evidence = known?.evidence?.kind === "recorded" ? known.evidence : null;
    // The timing run that recorded this model's evidence (same source) gives the result.
    const timing = evidence?.source ? ws.runs.find(run => run.kind === "timing" && run.provenance.kind === "recorded" && run.provenance.source === evidence.source) ?? null : null;
    if (timing && timing.status !== "queued" && timing.status !== "running" && timing.status !== "cancelled") {
      const kept = timing.status === "completed" || timing.status === "passed-gate";
      const speed = draft.routing.fallbackAction.speedFactor;
      const cover = !kept && mode === "hybrid" ? ` As the fallback it only covers link loss${finite(speed) ? `, at ${fmtNumber(speed, 2)}× speed` : ""}.` : "";
      return { id: "policy-rate", verdict: kept ? "pass" : "warn", title, detail: `${model.name}: Run ${timing.number} (${timing.title}) ${STATUS_TEXT[timing.status]}.${timing.note ? ` ${timing.note}` : ""}${cover}`, evidence: timing.provenance };
    }
    if (known && evidence && known.state === "fallback-only") return { id: "policy-rate", verdict: "warn", title, detail: `${model.name}: ${known.detail ?? "runs only as a fallback on this device"}.`, evidence };
    return { id: "policy-rate", verdict: "pending", title, detail: `No recorded timing shows ${model.name} at the ${rate === null ? "robot’s" : `${fmtNumber(rate)} Hz`} control rate on the ${hardware}.`, evidence: NOT_REPORTED };
  }
  const policy = draft.cloudModels.find(model => model.role === "policy");
  if (mode === "edge-only" || !policy) return null;
  const title = "Action policy at the control rate";
  if (!policy.chunk || rate === null) return { id: "policy-rate", verdict: "pending", title, detail: "The cloud policy’s chunk rate or the robot’s control rate is not declared.", evidence: NOT_REPORTED };
  const contract = `${fmtCount(policy.chunk.actions)}-action chunks at ${fmtNumber(policy.chunk.rateHz)} Hz`;
  if (!policy.evidence) return { id: "policy-rate", verdict: "pending", title, detail: `Declared: ${contract} for the robot’s ${fmtNumber(rate)} Hz control rate; no evidence is stored for this policy yet.`, evidence: NOT_REPORTED };
  return policy.chunk.rateHz >= rate
    ? { id: "policy-rate", verdict: "pass", title, detail: `The cloud policy streams ${contract} for the robot’s ${fmtNumber(rate)} Hz control rate; nothing on the ${hardware} runs at the control rate.`, evidence: policy.evidence }
    : { id: "policy-rate", verdict: "warn", title, detail: `The cloud policy streams ${contract}, below the robot’s ${fmtNumber(rate)} Hz control rate.`, evidence: policy.evidence };
}

function cloudRttCheck(draft: ConfigurationDraft): CompatibilityCheck | null {
  const { mode, fallbackTrigger, fallbackAction } = draft.routing;
  if (mode === "edge-only" || !draft.cloudModels.length) return null;
  const rtt = fallbackTrigger.cloudRttP95Ms, validity = fallbackAction.validityMs;
  const title = mode === "hybrid" && rtt !== null ? `Cloud round trip against the ${fmtNumber(rtt, 0)} ms fallback trigger`
    : validity !== null ? `Cloud round trip within the ${fmtNumber(validity, 0)} ms action validity` : "Cloud round trip";
  return { id: "cloud-rtt", verdict: "pending", title, detail: "No cloud endpoint has been deployed for this configuration.", evidence: NOT_REPORTED, action: { label: "Measure after the first deploy", href: null } };
}

/** Highest recorded board input among robots that store recorded telemetry on this edge hardware. */
function recordedPowerPeak(ws: ConvoyWorkspace, hardware: string): { robot: string; watts: number; provenance: StoredProvenance } | null {
  let best: { robot: string; watts: number; provenance: StoredProvenance } | null = null;
  for (const robot of ws.robots) {
    const telemetry = robot.telemetry;
    if (!telemetry || telemetry.provenance.kind !== "recorded" || !robot.configId) continue;
    const config = getConfiguration(ws, robot.configId);
    if ((config ? getRevision(config, robot.rev) : null)?.edgeHardware.name !== hardware) continue;
    const values = [telemetry.latest.boardPowerPeakW, telemetry.latest.boardPowerW, ...(telemetry.day?.boardPowerW?.values ?? []), ...(telemetry.recent?.boardPowerW?.values ?? [])].filter(finite);
    if (!values.length) continue;
    const watts = Math.max(...values);
    if (!best || watts > best.watts) best = { robot: robot.name, watts, provenance: telemetry.provenance };
  }
  return best;
}

function powerCheck(ws: ConvoyWorkspace, draft: ConfigurationDraft): CompatibilityCheck | null {
  const hardware = draft.edgeHardware, mode = selectedPowerMode(hardware);
  if (!mode) return null;
  const title = `Board power within the ${mode.label} mode`;
  const peak = recordedPowerPeak(ws, hardware.name);
  if (mode.capW === null) return { id: "power", verdict: "warn", title, detail: `${mode.label} has no power cap, so board input is limited only by the thermal guard${peak ? `; recorded board input peaked at ${fmtFixed(peak.watts)} W on ${peak.robot}` : ""}.`, evidence: peak?.provenance ?? NOT_REPORTED };
  if (!peak) return { id: "power", verdict: "pending", title, detail: `Board input has not been recorded on the ${hardware.name} against the ${fmtNumber(mode.capW)} W cap.`, evidence: NOT_REPORTED };
  const share = peak.watts / mode.capW * 100, warnAt = draft.flagRules.boardPowerWarnPctOfCap ?? 100;
  const warn = share >= warnAt;
  return { id: "power", verdict: warn ? "warn" : "pass", title, detail: `Recorded board input peaked at ${fmtFixed(peak.watts)} W on ${peak.robot}, ${fmtPct(share, 0)} of the ${fmtNumber(mode.capW)} W cap${warn ? `, at or above the ${fmtNumber(warnAt, 0)} % warning rule` : ""}.`, evidence: peak.provenance };
}

function memoryCheck(draft: ConfigurationDraft): CompatibilityCheck | null {
  if (!draft.edgeModels.length) return null;
  const hardware = draft.edgeHardware, title = "Memory with every edge model resident";
  const memory = residentMemory(draft);
  if (memory.total === null) return { id: "memory", verdict: "pending", title, detail: `Resident memory is not reported for ${memory.missing.map(model => model.name).join(", ")}.`, evidence: NOT_REPORTED };
  const roles = draft.edgeModels.map(model => model.role);
  const parts = draft.edgeModels.map(model => `${roles.filter(role => role === model.role).length > 1 ? model.shortName : MODEL_ROLE_LABEL[model.role].toLowerCase()} ${gib(model.residentGiB ?? 0)}`).join(", ");
  const freePct = (hardware.memoryGiB - memory.total) / hardware.memoryGiB * 100;
  const warnPct = draft.flagRules.memoryAvailableWarnPct;
  const verdict: CheckVerdict = memory.total > hardware.memoryGiB ? "block" : warnPct !== null && freePct < warnPct ? "warn" : "pass";
  const note = verdict === "block" ? ` That is more than the ${hardware.name} has.` : verdict === "warn" ? ` Less than ${fmtNumber(warnPct, 0)} % stays free for the system, agent and cameras.` : "";
  return { id: "memory", verdict, title, detail: `${gib(memory.total)} of ${gib(hardware.memoryGiB)} GiB: ${parts} GiB.${note} Estimated from each model’s declared resident memory.`, evidence: ESTIMATE };
}

/**
 * The draft's compatibility check: per computed check, the stored check of a
 * revision with the same premise on this hardware, else a computed one; then the
 * starting revision's other stored checks (device evidence such as a model that
 * was refused). Ordered planner, device evidence, policy rate, cloud, power, memory.
 */
export function compatibilityFor(ws: ConvoyWorkspace, draft: ConfigurationDraft): CompatibilityCheck[] {
  const library = storedChecks(ws, draft);
  const reuse = (id: ComputedId) => library.find(entry => entry.check.id === id && premise(id, entry.stack) === premise(id, draft))?.check ?? null;
  const compute: Record<ComputedId, () => CompatibilityCheck | null> = {
    "planner-fit": () => plannerCheck(ws, draft), "policy-rate": () => policyRateCheck(ws, draft), "cloud-rtt": () => cloudRttCheck(draft),
    power: () => powerCheck(ws, draft), memory: () => memoryCheck(draft),
  };
  const checks = COMPUTED_CHECKS.map(id => reuse(id) ?? compute[id]());
  const carried = library.filter(entry => entry.base && !isComputed(entry.check.id)).map(entry => entry.check);
  return [checks[0], ...carried, ...checks.slice(1)].filter((check): check is CompatibilityCheck => check !== null);
}

export function checkCounts(checks: readonly CompatibilityCheck[]): Record<CheckVerdict, number> {
  const counts: Record<CheckVerdict, number> = { pass: 0, warn: 0, block: 0, pending: 0 };
  for (const check of checks) counts[check.verdict]++;
  return counts;
}
/** "3 pass · 1 warning · 1 blocked · 1 not measured" (zero counts left out). */
export function checkSummary(checks: readonly CompatibilityCheck[]): string {
  const counts = checkCounts(checks);
  const parts = [counts.pass && `${counts.pass} pass`, counts.warn && `${counts.warn} ${counts.warn === 1 ? "warning" : "warnings"}`, counts.block && `${counts.block} blocked`, counts.pending && `${counts.pending} not measured`].filter(Boolean);
  return parts.join(" · ") || "No checks";
}

/* ---------- saving ---------- */

/** The revision the draft describes. Edge-only keeps no cloud models; the edge memory budget is the declared total. */
export function draftRevision(draft: ConfigurationDraft, rev: string, createdAt: string, compatibility: CompatibilityCheck[]): ConfigRevision {
  const blendInto = draft.routing.fallbackAction.blendInto?.trim() || null;
  const note = draft.note.trim();
  return {
    rev, createdAt, ...(note ? { note } : {}),
    robot: draft.robot,
    edgeHardware: { ...draft.edgeHardware, edgeBudgetGiB: residentMemory(draft).total },
    edgeModels: draft.edgeModels,
    cloudModels: draft.routing.mode === "edge-only" ? [] : draft.cloudModels,
    routing: {
      ...draft.routing,
      fallbackAction: { ...draft.routing.fallbackAction, blendInto },
      escalation: { ...draft.routing.escalation, channel: draft.routing.escalation.channel.trim() },
      thermalGuard: { ...draft.routing.thermalGuard, action: draft.routing.thermalGuard.action.trim() },
    },
    safety: draft.safety, flagRules: draft.flagRules, compatibility,
  };
}

/**
 * Adds the next revision to a configuration as the revision under test. The
 * production revision and every robot keep the revision they run; the name and
 * purpose change for the whole configuration when given.
 */
export function addRevision(ws: ConvoyWorkspace, configId: string, input: { revision: ConfigRevision; name?: string; purpose?: string; from?: string | null }, now: number | Date): ConvoyWorkspace {
  const config = ws.configurations.find(item => item.id === configId);
  if (!config) throw new Error(`Unknown configuration "${configId}".`);
  if (config.revisions.some(revision => revision.rev === input.revision.rev)) throw new Error(`${config.name} already has a revision ${input.revision.rev}.`);
  const at = new Date(now).toISOString();
  const name = input.name?.trim() || config.name;
  const updated: Configuration = { ...config, name, purpose: input.purpose ?? config.purpose, revisions: [...config.revisions, input.revision], candidateRev: input.revision.rev, updatedAt: at };
  const event: ActivityEvent = {
    id: uniqueId(`act-configuration-created-${new Date(now).getTime().toString(36)}`, ws.activity.map(item => item.id)), at, kind: "configuration-created",
    subject: { type: "configuration", id: configId }, configId, message: `${name} ${input.revision.rev} created${input.from ? ` from ${input.from}` : ""}`,
    provenance: { kind: "recorded", at, source: "Workspace change" },
  };
  return { ...ws, meta: { ...ws.meta, updatedAt: at }, configurations: ws.configurations.map(item => item.id === configId ? updated : item), activity: [event, ...ws.activity] };
}

/**
 * Saves the draft into a workspace: a new configuration (r1, draft) or the next
 * revision of the one it started from. Throws while the draft has issues. Pass it
 * to `save()` inside an updater so a conflict re-applies it to the fresh document.
 */
export function applyDraft(ws: ConvoyWorkspace, draft: ConfigurationDraft, now: number | Date): { workspace: ConvoyWorkspace; configId: string; rev: string } {
  const issues = draftIssues(ws, draft);
  if (issues.length) throw new Error(`The configuration is not complete: ${issues[0].message}`);
  const at = new Date(now).toISOString();
  const compatibility = compatibilityFor(ws, draft);
  const config = getConfiguration(ws, draft.baseId);
  if (draft.saveAs === "revision" && config) {
    const rev = nextRevision(config);
    const workspace = addRevision(ws, config.id, { revision: draftRevision(draft, rev, at, compatibility), name: draft.name, purpose: draft.purpose.trim(), from: draft.baseRev }, now);
    return { workspace, configId: config.id, rev };
  }
  const { workspace, configuration } = createConfiguration(ws, { name: draft.name.trim(), purpose: draft.purpose.trim(), revision: draftRevision(draft, "r1", at, compatibility), suiteId: draft.suiteId }, now);
  return { workspace, configId: configuration.id, rev: configuration.candidateRev ?? "r1" };
}

/** The revision label the draft will get when saved now. */
export function draftRevisionLabel(ws: ConvoyWorkspace, draft: ConfigurationDraft): string {
  const config = draft.saveAs === "revision" ? getConfiguration(ws, draft.baseId) : null;
  return config ? nextRevision(config) : "r1";
}

/** What creating the draft does, for the note beside the stepper. */
export function createOutcome(ws: ConvoyWorkspace, draft: ConfigurationDraft): string[] {
  const config = draft.saveAs === "revision" ? getConfiguration(ws, draft.baseId) : null;
  const lines: string[] = [];
  if (config) {
    const rev = nextRevision(config);
    lines.push(`${rev} becomes the revision under test${config.productionRev ? `; production stays on ${config.productionRev} until you promote ${rev}` : ""}.`);
    const running = [...new Set(robotsFor(ws, config.id).map(robot => robot.rev).filter((value): value is string => !!value))].toSorted();
    lines.push(running.length ? `Robots stay on ${running.join(" and ")} until you move them to ${rev}.` : "No robots run this configuration yet.");
  } else {
    lines.push("It is saved as a draft with revision r1. No robots are attached until you add them from its dashboard.");
  }
  const suite = getSuite(ws, draft.suiteId);
  if (suite) {
    lines.push(`Evaluation: queue ${suite.name} ${suite.version} (${fmtCount(suite.episodesPerRun)} episodes per run) on a test robot; nothing runs on its own.`);
    if (suite.gate.length) {
      const criteria = suite.gate.slice(0, 3).map(criterion => `${criterion.label.toLowerCase()}${/^[≥≤<>=]/.test(criterion.target) ? ` ${criterion.target}` : `: ${criterion.target.toLowerCase()}`}`);
      const more = suite.gate.length - criteria.length;
      lines.push(`Production needs a passing gate: ${criteria.join(", ")}${more > 0 ? `, and ${more} more ${more === 1 ? "criterion" : "criteria"}` : ""}.`);
    }
  }
  return lines;
}
