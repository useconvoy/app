/**
 * Pure document changes for the UI actions: create or delete a configuration,
 * add or remove a robot. Each returns a new workspace and appends an activity
 * event; pass them to `save()` as updaters:
 *
 *   await save(current => addRobot(current, { configId, name: "Bench 01", deviceId }, Date.now()).workspace);
 *
 * Actions are recorded facts about the document (provenance "recorded" with the
 * time of the change). They never add measured values.
 */
import { ID_PATTERN, WORKSPACE_SCHEMA_VERSION } from "./types";
import type { ActivityEvent, ConfigRevision, Configuration, ConvoyWorkspace, Robot, StoredProvenance } from "./types";
import { RESERVED_KEYS } from "./validate";

const recorded = (at: string, source: string): StoredProvenance => ({ kind: "recorded", at, source });
/** Placeholder for declared text the forms do not ask for. */
export const NOT_SPECIFIED = "Not specified";
const iso = (now: number | Date) => new Date(now).toISOString();
/** Longest id (`ID_PATTERN`). */
const MAX_ID = 64;
/** The activity feed keeps the newest events only, so the document does not grow with every change. */
export const ACTIVITY_LIMIT = 200;

/** Route-safe id from a name: "Edge planner · r1" → "edge-planner-r1". */
export function slugify(text: string): string {
  return text.toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48) || "item";
}
/**
 * `base` (slugified) made unique among `taken` with -2, -3, …, never longer than
 * 64 characters ("new" is reserved for a route; `__proto__`, `constructor` and
 * `prototype` are never ids).
 */
export function uniqueId(base: string, taken: Iterable<string>): string {
  const used = new Set(taken);
  const root = ID_PATTERN.test(base) ? base : slugify(base);
  if (!used.has(root) && root !== "new" && !RESERVED_KEYS.has(root)) return root;
  for (let i = 2; ; i++) {
    const suffix = `-${i}`;
    const candidate = `${root.slice(0, MAX_ID - suffix.length)}${suffix}`;
    if (!used.has(candidate)) return candidate;
  }
}

/** A workspace with nothing in it: where an account's own document starts (the sample is never saved). */
export function emptyWorkspace(now: number | Date): ConvoyWorkspace {
  return {
    schemaVersion: WORKSPACE_SCHEMA_VERSION,
    meta: { id: "workspace", name: "Workspace", label: "Workspace", description: "", updatedAt: iso(now) },
    configurations: [], robots: [], suites: [], runs: [], rollouts: [], traces: [], logs: [], activity: [],
  };
}

function withActivity(ws: ConvoyWorkspace, event: Omit<ActivityEvent, "id" | "provenance">, now: number | Date): ConvoyWorkspace {
  const id = uniqueId(`act-${event.kind}-${new Date(now).getTime().toString(36)}`, ws.activity.map(item => item.id));
  const activity = [{ ...event, id, provenance: recorded(event.at, "Workspace change") }, ...ws.activity].slice(0, ACTIVITY_LIMIT);
  return { ...ws, meta: { ...ws.meta, updatedAt: iso(now) }, activity };
}
const touch = (config: Configuration, now: number | Date): Configuration => ({ ...config, updatedAt: iso(now) });

/** Adds a configuration with its first revision, under test unless a status is given. */
export function createConfiguration(ws: ConvoyWorkspace, input: { name: string; purpose?: string; revision: ConfigRevision; status?: Configuration["status"]; suiteId?: string | null; id?: string }, now: number | Date): { workspace: ConvoyWorkspace; configuration: Configuration } {
  const at = iso(now);
  const configuration: Configuration = {
    id: uniqueId(input.id ?? input.name, ws.configurations.map(config => config.id)), name: input.name.trim(), purpose: input.purpose ?? "",
    status: input.status ?? "testing", recommended: false, productionRev: null, candidateRev: input.revision.rev,
    revisions: [{ ...input.revision, createdAt: input.revision.createdAt || at }], suiteId: input.suiteId ?? null, createdAt: at, updatedAt: at,
  };
  const workspace = withActivity({ ...ws, configurations: [...ws.configurations, configuration] },
    { at, kind: "configuration-created", subject: { type: "configuration", id: configuration.id }, configId: configuration.id, message: `${configuration.name} created` }, now);
  return { workspace, configuration };
}

/** Deletes a configuration with its robots and everything stored about them (runs, rollouts, traces, logs, activity). */
export function deleteConfiguration(ws: ConvoyWorkspace, configId: string, now: number | Date): ConvoyWorkspace {
  const config = ws.configurations.find(item => item.id === configId);
  if (!config) throw new Error(`Unknown configuration "${configId}".`);
  const robots = new Set(ws.robots.filter(robot => robot.configId === configId).map(robot => robot.id));
  const runs = new Set(ws.runs.filter(run => run.configId === configId || robots.has(run.robotId)).map(run => run.id));
  return {
    ...ws,
    meta: { ...ws.meta, updatedAt: iso(now) },
    configurations: ws.configurations.filter(item => item.id !== configId),
    robots: ws.robots.filter(robot => !robots.has(robot.id)),
    runs: ws.runs.filter(run => !runs.has(run.id)).map(run => run.baselineRunId && runs.has(run.baselineRunId) ? { ...run, baselineRunId: null } : run),
    rollouts: ws.rollouts.filter(rollout => !runs.has(rollout.runId)),
    traces: ws.traces.filter(trace => !robots.has(trace.robotId)),
    logs: ws.logs.filter(line => line.configId !== configId && !(line.robotId && robots.has(line.robotId))),
    activity: ws.activity.filter(event => event.configId !== configId),
  };
}

export interface NewRobot {
  configId: string;
  name: string;
  /** A control-plane device id (or `CONFIGURED_DEVICE`) for live telemetry and traces. */
  deviceId?: string | null;
  /** A control-plane project whose evaluations and episodes are this robot's evals. */
  projectId?: string | null;
  platformRobotId?: string | null;
  /** Offline evaluations (`oev_…`) shown as this robot's evals, tagged "Offline sim". */
  offlineEvaluationIds?: readonly string[] | null;
}

/** Adds a test robot to a configuration's revision under test (else its newest revision). */
export function addRobot(ws: ConvoyWorkspace, input: NewRobot, now: number | Date): { workspace: ConvoyWorkspace; robot: Robot } {
  const config = ws.configurations.find(item => item.id === input.configId);
  if (!config) throw new Error(`Unknown configuration "${input.configId}".`);
  const name = input.name.trim();
  if (!name) throw new Error("A robot needs a name.");
  const at = iso(now);
  const robot: Robot = {
    // "Not specified" keeps the document readable by earlier versions of this site, which require a site.
    id: uniqueId(name, ws.robots.map(item => item.id)), name, configId: config.id, role: "test", site: NOT_SPECIFIED,
    rev: config.candidateRev ?? config.productionRev ?? config.revisions[config.revisions.length - 1].rev,
    ...(input.deviceId ? { deviceId: input.deviceId } : {}),
    ...(input.projectId ? { projectId: input.projectId, ...(input.platformRobotId ? { platformRobotId: input.platformRobotId } : {}) } : {}),
    ...(input.offlineEvaluationIds?.length ? { offlineEvaluationIds: [...new Set(input.offlineEvaluationIds)] } : {}),
    kind: input.deviceId ? "bench" : "simulator", health: "not-reported", healthReason: null, flags: [], registeredAt: at,
  };
  const workspace = withActivity({
    ...ws,
    robots: [...ws.robots, robot],
    configurations: ws.configurations.map(item => item.id === config.id ? touch(item, now) : item),
  }, { at, kind: "robot-added", subject: { type: "robot", id: robot.id }, configId: config.id, message: `${robot.name} added to ${config.name}` }, now);
  return { workspace, robot };
}

/** Sets the offline evaluations a robot shows as evals; an empty list removes the link. */
export function linkOfflineEvaluations(ws: ConvoyWorkspace, robotId: string, ids: readonly string[], now: number | Date): ConvoyWorkspace {
  const robot = ws.robots.find(item => item.id === robotId);
  if (!robot) throw new Error(`Unknown robot "${robotId}".`);
  const next: Robot = { ...robot, offlineEvaluationIds: [...new Set(ids)] };
  if (!next.offlineEvaluationIds?.length) delete next.offlineEvaluationIds;
  return {
    ...ws,
    meta: { ...ws.meta, updatedAt: iso(now) },
    robots: ws.robots.map(item => item.id === robotId ? next : item),
    configurations: ws.configurations.map(item => item.id === robot.configId ? touch(item, now) : item),
  };
}

/** Removes a robot with its stored runs, rollouts, traces and logs. */
export function removeRobot(ws: ConvoyWorkspace, robotId: string, now: number | Date): ConvoyWorkspace {
  const robot = ws.robots.find(item => item.id === robotId);
  if (!robot) throw new Error(`Unknown robot "${robotId}".`);
  const runs = new Set(ws.runs.filter(run => run.robotId === robotId).map(run => run.id));
  return {
    ...ws,
    meta: { ...ws.meta, updatedAt: iso(now) },
    robots: ws.robots.filter(item => item.id !== robotId),
    runs: ws.runs.filter(run => !runs.has(run.id)).map(run => run.baselineRunId && runs.has(run.baselineRunId) ? { ...run, baselineRunId: null } : run),
    rollouts: ws.rollouts.filter(rollout => !runs.has(rollout.runId)),
    traces: ws.traces.filter(trace => trace.robotId !== robotId),
    logs: ws.logs.filter(line => line.robotId !== robotId),
    configurations: ws.configurations.map(item => item.id === robot.configId ? touch(item, now) : item),
  };
}
