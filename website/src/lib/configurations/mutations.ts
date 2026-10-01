/**
 * Pure document changes for the UI actions (create a configuration, attach a
 * robot, flag or clear a flag, queue an evaluation). Each returns a new
 * workspace and appends an activity event; pass them to `save()` as updaters:
 *
 *   await save(current => attachRobot(current, "unit-18", { configId, role: "test", site, rev }, Date.now()));
 *
 * Actions are recorded facts about the document (provenance "recorded" with the
 * time of the change). They never add measured values.
 */
import { ID_PATTERN } from "./types";
import type { ActivityEvent, ConfigRevision, Configuration, ConvoyWorkspace, EvalRun, Flag, FlagSeverity, RobotRole, StoredProvenance } from "./types";

const recorded = (at: string, source: string): StoredProvenance => ({ kind: "recorded", at, source });
const iso = (now: number | Date) => new Date(now).toISOString();

/** Route-safe id from a name: "Bimanual station · Edge" → "bimanual-station-edge". */
export function slugify(text: string): string {
  return text.toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48) || "item";
}
/** `base` (slugified) made unique among `taken` with -2, -3, … ("new" is reserved for a route). */
export function uniqueId(base: string, taken: Iterable<string>): string {
  const used = new Set(taken);
  const root = ID_PATTERN.test(base) ? base : slugify(base);
  if (!used.has(root) && root !== "new") return root;
  for (let i = 2; ; i++) { const candidate = `${root.slice(0, 60)}-${i}`; if (!used.has(candidate)) return candidate; }
}
export function nextRunNumber(ws: ConvoyWorkspace): number {
  return ws.runs.reduce((max, run) => Math.max(max, run.number), 0) + 1;
}
function withActivity(ws: ConvoyWorkspace, event: Omit<ActivityEvent, "id" | "provenance">, now: number | Date): ConvoyWorkspace {
  const id = uniqueId(`act-${event.kind}-${new Date(now).getTime().toString(36)}`, ws.activity.map(item => item.id));
  return { ...ws, meta: { ...ws.meta, updatedAt: iso(now) }, activity: [{ ...event, id, provenance: recorded(event.at, "Workspace change") }, ...ws.activity] };
}
const touch = (config: Configuration, now: number | Date): Configuration => ({ ...config, updatedAt: iso(now) });

/** Adds a configuration with its first revision (status draft unless given). */
export function createConfiguration(ws: ConvoyWorkspace, input: { name: string; purpose: string; revision: ConfigRevision; status?: Configuration["status"]; suiteId?: string | null; id?: string }, now: number | Date): { workspace: ConvoyWorkspace; configuration: Configuration } {
  const at = iso(now);
  const configuration: Configuration = {
    id: uniqueId(input.id ?? input.name, ws.configurations.map(config => config.id)), name: input.name, purpose: input.purpose,
    status: input.status ?? "draft", recommended: false, productionRev: null, candidateRev: input.revision.rev,
    revisions: [{ ...input.revision, createdAt: input.revision.createdAt || at }], suiteId: input.suiteId ?? ws.suites[0]?.id ?? null, createdAt: at, updatedAt: at,
  };
  const workspace = withActivity({ ...ws, configurations: [...ws.configurations, configuration] },
    { at, kind: "configuration-created", subject: { type: "configuration", id: configuration.id }, configId: configuration.id, message: `${configuration.name} ${configuration.candidateRev} created as a ${configuration.status === "draft" ? "draft" : configuration.status}` }, now);
  return { workspace, configuration };
}

/** Attaches a registered robot to a configuration revision with a role and site. */
export function attachRobot(ws: ConvoyWorkspace, robotId: string, input: { configId: string; role: RobotRole; site: string; rev: string }, now: number | Date): ConvoyWorkspace {
  const robot = ws.robots.find(item => item.id === robotId);
  const config = ws.configurations.find(item => item.id === input.configId);
  if (!robot) throw new Error(`Unknown robot "${robotId}".`);
  if (!config || !config.revisions.some(revision => revision.rev === input.rev)) throw new Error(`Unknown configuration revision "${input.configId} ${input.rev}".`);
  const at = iso(now);
  return withActivity({
    ...ws,
    robots: ws.robots.map(item => item.id === robotId ? { ...item, configId: input.configId, role: input.role, site: input.site.trim() || item.site, rev: input.rev } : item),
    configurations: ws.configurations.map(item => item.id === input.configId ? touch(item, now) : item),
  }, { at, kind: "robot-added", subject: { type: "robot", id: robotId }, configId: input.configId, message: `${robot.name} added to ${config.name} ${input.rev} as ${input.role === "test" ? "a test robot" : "a production robot"}` }, now);
}

/** Moves an attached robot between test and production (the page checks the gate before promoting). */
export function setRobotRole(ws: ConvoyWorkspace, robotId: string, role: RobotRole, now: number | Date): ConvoyWorkspace {
  const robot = ws.robots.find(item => item.id === robotId);
  if (!robot || !robot.configId) throw new Error(`Robot "${robotId}" is not attached to a configuration.`);
  if (robot.role === role) return ws;
  const at = iso(now);
  return withActivity({ ...ws, robots: ws.robots.map(item => item.id === robotId ? { ...item, role } : item) },
    { at, kind: "role-changed", subject: { type: "robot", id: robotId }, configId: robot.configId, message: `${robot.name} moved to ${role}` }, now);
}

/** Promotes a revision: it becomes the production revision and the configuration's production robots run it. */
export function promoteRevision(ws: ConvoyWorkspace, configId: string, rev: string, now: number | Date): ConvoyWorkspace {
  const config = ws.configurations.find(item => item.id === configId);
  if (!config || !config.revisions.some(revision => revision.rev === rev)) throw new Error(`Unknown configuration revision "${configId} ${rev}".`);
  const at = iso(now);
  return withActivity({
    ...ws,
    configurations: ws.configurations.map(item => item.id === configId ? { ...touch(item, now), status: "production", productionRev: rev, candidateRev: item.candidateRev === rev ? null : item.candidateRev } : item),
    robots: ws.robots.map(item => item.configId === configId && item.role === "production" ? { ...item, rev } : item),
  }, { at, kind: "promoted", subject: { type: "configuration", id: configId }, configId, message: `${config.name} ${rev} promoted to production` }, now);
}

/** Adds a manual flag with the operator's note. */
export function flagRobot(ws: ConvoyWorkspace, robotId: string, input: { label: string; note: string; severity?: FlagSeverity; by?: string }, now: number | Date): ConvoyWorkspace {
  const robot = ws.robots.find(item => item.id === robotId);
  if (!robot) throw new Error(`Unknown robot "${robotId}".`);
  const at = iso(now);
  const flag: Flag = {
    id: uniqueId(`${robotId}-manual-${new Date(now).getTime().toString(36)}`, robot.flags.map(item => item.id)), rule: "manual", severity: input.severity ?? "warning",
    label: input.label, detail: input.note, note: input.note, at, ...(input.by ? { by: input.by } : {}), provenance: recorded(at, input.by ? `Flagged by ${input.by}` : "Manual flag"),
  };
  return withActivity({ ...ws, robots: ws.robots.map(item => item.id === robotId ? { ...item, flags: [...item.flags, flag] } : item) },
    { at, kind: "flagged", subject: { type: "robot", id: robotId }, configId: robot.configId, message: `${robot.name} flagged: ${input.label}` }, now);
}

/** Removes a stored flag. */
export function clearFlag(ws: ConvoyWorkspace, robotId: string, flagId: string, now: number | Date): ConvoyWorkspace {
  const robot = ws.robots.find(item => item.id === robotId);
  const flag = robot?.flags.find(item => item.id === flagId);
  if (!robot || !flag) throw new Error(`Unknown flag "${flagId}".`);
  const at = iso(now);
  return withActivity({ ...ws, robots: ws.robots.map(item => item.id === robotId ? { ...item, flags: item.flags.filter(entry => entry.id !== flagId) } : item) },
    { at, kind: "flag-cleared", subject: { type: "robot", id: robotId }, configId: robot.configId, message: `${robot.name}: cleared “${flag.label}”` }, now);
}

/** Queues a suite run on a robot. The run has no results until a runner reports them. */
export function queueEvaluation(ws: ConvoyWorkspace, input: { configId: string; rev: string; robotId: string; suiteId: string; variant: string; purpose?: string }, now: number | Date): { workspace: ConvoyWorkspace; run: EvalRun } {
  const suite = ws.suites.find(item => item.id === input.suiteId);
  if (!suite) throw new Error(`Unknown suite "${input.suiteId}".`);
  const number = nextRunNumber(ws);
  const at = iso(now);
  const run: EvalRun = {
    id: uniqueId(`run-${number}`, ws.runs.map(item => item.id)), number, kind: "suite", title: `${suite.name} ${suite.version}`, suiteId: suite.id,
    configId: input.configId, rev: input.rev, variant: input.variant, robotId: input.robotId, ...(input.purpose ? { purpose: input.purpose } : {}),
    status: "queued", progress: { done: 0, total: suite.episodesPerRun }, startedAt: null, finishedAt: null, simEngine: suite.simEngine,
    counts: { episodes: 0, successes: null }, successCi95: null, safety: null, episodeTime: null, perTask: [], slices: [], failureModes: [], gate: null,
    provenance: { kind: "not-reported" }, note: `Queued ${at}`,
  };
  const workspace = withActivity({ ...ws, runs: [...ws.runs, run], configurations: ws.configurations.map(item => item.id === input.configId ? touch(item, now) : item) },
    { at, kind: "run-queued", subject: { type: "run", id: run.id }, configId: input.configId, message: `Run ${number} queued · ${suite.name} ${suite.version} on ${input.rev}` }, now);
  return { workspace, run };
}
