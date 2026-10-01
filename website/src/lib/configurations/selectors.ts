/**
 * Pure read helpers over a `ConvoyWorkspace` (re-exported from client.ts).
 * Live bindings are passed in (`LiveBinding`, keyed by robot id) so measured
 * values replace stored ones for bound robots and never mix with them.
 */
import { fmtFixed, fmtMs, fmtPct, seriesPoints } from "./format";
import type { LiveBinding } from "./live";
import { routes } from "./routes";
import type {
  ActionTrace, ActivityEvent, ConfigRevision, ConfigStatus, Configuration, ConvoyWorkspace, EvalRun, EvalSuite, Flag, FlagRules,
  FlagSeverity, LatencySeries, LogLine, Percentiles, Provenance, Robot, RobotHealth, RobotRole, Rollout, SeriesPoint, TelemetryMetric, TelemetryReading, TraceSpan,
} from "./types";

export type LiveMap = Readonly<Record<string, LiveBinding | undefined>>;
const byTimeDesc = <T extends { at: string }>(a: T, b: T) => Date.parse(b.at) - Date.parse(a.at);
const collator = new Intl.Collator("en-US", { numeric: true, sensitivity: "base" });

/* ---------- configurations ---------- */

export type ConfigFilter = "all" | ConfigStatus;
export type ConfigSort = "activity" | "name" | "eval";

/** Text used by search: names, purpose, robot, hardware, models, robot names and sites. */
export function configurationSearchText(ws: ConvoyWorkspace, config: Configuration): string {
  const rev = currentRevision(config);
  return [config.name, config.purpose, rev.robot.name, rev.robot.summary, rev.edgeHardware.name, ...rev.edgeModels.map(m => m.name), ...rev.cloudModels.map(m => m.name),
    ...robotsFor(ws, config.id).flatMap(robot => [robot.name, robot.site, robot.deviceId ?? ""])].join(" \n").toLowerCase();
}

/** Configurations matching a search query (all words) and a status filter, sorted. */
export function listConfigurations(ws: ConvoyWorkspace, options: { query?: string; status?: ConfigFilter; sort?: ConfigSort } = {}): Configuration[] {
  const words = (options.query ?? "").toLowerCase().split(/\s+/).filter(Boolean);
  const status = options.status ?? "all";
  const matches = ws.configurations.filter(config =>
    (status === "all" || config.status === status || (status === "testing" && config.candidateRev !== null && config.status !== "draft"))
    && (!words.length || words.every(word => configurationSearchText(ws, config).includes(word))));
  const sort = options.sort ?? "activity";
  const evalRate = (config: Configuration) => { const run = latestGateRun(ws, config.id); return run ? successShare(run) ?? -1 : -1; };
  return matches.toSorted((a, b) => sort === "name" ? collator.compare(a.name, b.name)
    : sort === "eval" ? evalRate(b) - evalRate(a)
    : Date.parse(b.updatedAt) - Date.parse(a.updatedAt));
}
/** Count per status chip, using the same matching as `listConfigurations`. */
export function configurationCounts(ws: ConvoyWorkspace, query = ""): Record<ConfigFilter, number> {
  const count = (status: ConfigFilter) => listConfigurations(ws, { query, status }).length;
  return { all: count("all"), testing: count("testing"), production: count("production"), draft: count("draft") };
}
export function getConfiguration(ws: ConvoyWorkspace, configId: string): Configuration | null {
  return ws.configurations.find(config => config.id === configId) ?? null;
}
export function getRevision(config: Configuration, rev: string | null | undefined): ConfigRevision | null {
  return rev ? config.revisions.find(item => item.rev === rev) ?? null : null;
}
/** The revision under test, else the production revision, else the newest. */
export function currentRevision(config: Configuration): ConfigRevision {
  return getRevision(config, config.candidateRev) ?? getRevision(config, config.productionRev) ?? config.revisions[config.revisions.length - 1];
}
export function productionRevision(config: Configuration): ConfigRevision | null {
  return getRevision(config, config.productionRev);
}
/** The next revision label: one past the highest "rN" (r3, r4 → "r5"). */
export function nextRevision(config: Pick<Configuration, "revisions">): string {
  return `r${config.revisions.reduce((max, revision) => Math.max(max, Number(/(\d+)$/.exec(revision.rev)?.[1] ?? 0)), 0) + 1}`;
}

/* ---------- robots ---------- */

const roleOrder: Record<RobotRole, number> = { test: 0, production: 1 };
/** Robots attached to a configuration: live-bound first, then test before production, then by name. */
export function robotsFor(ws: ConvoyWorkspace, configId: string, options: { role?: RobotRole } = {}): Robot[] {
  return ws.robots.filter(robot => robot.configId === configId && (!options.role || robot.role === options.role))
    .toSorted((a, b) => Number(!!b.deviceId) - Number(!!a.deviceId) || roleOrder[a.role] - roleOrder[b.role] || collator.compare(a.name, b.name));
}
export function getRobot(ws: ConvoyWorkspace, robotId: string): Robot | null {
  return ws.robots.find(robot => robot.id === robotId) ?? null;
}
/** Registered robots not attached to any configuration (the Add robot choices). */
export function unassignedRobots(ws: ConvoyWorkspace): Robot[] {
  return ws.robots.filter(robot => robot.configId === null).toSorted((a, b) => collator.compare(a.name, b.name));
}
/** Robots with a live device binding. */
export function boundRobots(ws: ConvoyWorkspace, configId?: string): Robot[] {
  return ws.robots.filter(robot => !!robot.deviceId && (!configId || robot.configId === configId));
}

/* ---------- route resolution ---------- */

export function resolveRobotRoute(ws: ConvoyWorkspace, configId: string, robotId: string): { configuration: Configuration; robot: Robot } | null {
  const configuration = getConfiguration(ws, configId), robot = getRobot(ws, robotId);
  return configuration && robot && robot.configId === configId ? { configuration, robot } : null;
}
export function resolveRunRoute(ws: ConvoyWorkspace, configId: string, robotId: string, runId: string): { configuration: Configuration; robot: Robot; run: EvalRun; runConfiguration: Configuration | null } | null {
  const route = resolveRobotRoute(ws, configId, robotId), run = getRun(ws, runId);
  return route && run && run.robotId === robotId ? { ...route, run, runConfiguration: getConfiguration(ws, run.configId) } : null;
}
export function robotHref(robot: Robot, params?: Parameters<typeof routes.robot>[2]): string | null {
  return robot.configId ? routes.robot(robot.configId, robot.id, params) : null;
}
/** A run lives under the robot it ran on (which may belong to another configuration than the run). */
export function runHref(ws: ConvoyWorkspace, run: EvalRun, params?: Parameters<typeof routes.run>[3]): string | null {
  const robot = getRobot(ws, run.robotId);
  return robot?.configId ? routes.run(robot.configId, robot.id, run.id, params) : null;
}

/* ---------- evaluations ---------- */

const runOrder = (run: EvalRun) => run.status === "running" ? 0 : run.status === "queued" ? 1 : 2;
const runTime = (run: EvalRun) => Date.parse(run.finishedAt ?? run.startedAt ?? "") || 0;
/** Runs, running and queued first, then newest first. Filter by robot, configuration and/or revision. */
export function runsFor(ws: ConvoyWorkspace, filter: { robotId?: string; configId?: string; rev?: string } = {}): EvalRun[] {
  return ws.runs.filter(run => (!filter.robotId || run.robotId === filter.robotId) && (!filter.configId || run.configId === filter.configId) && (!filter.rev || run.rev === filter.rev))
    .toSorted((a, b) => runOrder(a) - runOrder(b) || runTime(b) - runTime(a) || b.number - a.number);
}
export function getRun(ws: ConvoyWorkspace, runId: string): EvalRun | null {
  return ws.runs.find(run => run.id === runId) ?? null;
}
export function getSuite(ws: ConvoyWorkspace, suiteId: string | null | undefined): EvalSuite | null {
  return suiteId ? ws.suites.find(suite => suite.id === suiteId) ?? null : null;
}
/** Newest finished suite run with a gate decision for a configuration (optionally one revision). */
export function latestGateRun(ws: ConvoyWorkspace, configId: string, rev?: string | null): EvalRun | null {
  return runsFor(ws, { configId, ...(rev ? { rev } : {}) }).find(run => run.kind === "suite" && run.gate !== null) ?? null;
}
/** Success share 0–1, or null when not scored. */
export function successShare(run: Pick<EvalRun, "counts">): number | null {
  return run.counts.successes === null || !run.counts.episodes ? null : run.counts.successes / run.counts.episodes;
}
export type RolloutFilter = "all" | "failed" | "safety";
export function rolloutsFor(ws: ConvoyWorkspace, runId: string, filter: { outcome?: RolloutFilter; sliceId?: string | null; taskId?: string | null } = {}): Rollout[] {
  return ws.rollouts.filter(rollout => rollout.runId === runId
    && (!filter.outcome || filter.outcome === "all" || (filter.outcome === "failed" ? rollout.outcome !== "succeeded" : rollout.violations.length > 0 || rollout.outcome === "safety-stop"))
    && (!filter.sliceId || rollout.sliceIds.includes(filter.sliceId))
    && (!filter.taskId || rollout.taskId === filter.taskId));
}
export function getRollout(ws: ConvoyWorkspace, rolloutId: string): Rollout | null {
  return ws.rollouts.find(rollout => rollout.id === rolloutId) ?? null;
}
/** Slice name and family for a slice id within a suite. */
export function sliceInfo(suite: EvalSuite | null, sliceId: string): { name: string; family: string } {
  for (const family of suite?.sliceFamilies ?? []) {
    const slice = family.slices.find(item => item.id === sliceId);
    if (slice) return { name: slice.name, family: family.name };
  }
  return { name: sliceId, family: "" };
}
export function taskName(suite: EvalSuite | null, taskId: string): string {
  return suite?.tasks.find(task => task.id === taskId)?.name ?? taskId;
}

/* ---------- traces, logs, activity ---------- */

export type TraceFilter = "all" | "fallback" | "escalated" | "failed";
export function traceCategory(trace: ActionTrace): Exclude<TraceFilter, "all"> | null {
  return trace.outcome === "failed" ? "failed" : trace.escalated ? "escalated" : trace.path === "fallback" ? "fallback" : null;
}
/** Action traces for a robot, newest first. */
export function tracesFor(ws: ConvoyWorkspace, robotId: string, filter: TraceFilter = "all"): ActionTrace[] {
  return ws.traces.filter(trace => trace.robotId === robotId && (filter === "all" || (filter === "failed" ? trace.outcome === "failed" : filter === "escalated" ? trace.escalated : trace.path === "fallback")))
    .toSorted(byTimeDesc);
}
export function getTrace(ws: ConvoyWorkspace, traceId: string): ActionTrace | null {
  return ws.traces.find(trace => trace.id === traceId) ?? null;
}
/** Spans for one waterfall of a trace: its group's spans (spans without a group belong to the first group). */
export function spansForGroup(trace: ActionTrace, groupId?: string | null): TraceSpan[] {
  const first = trace.spanGroups?.[0]?.id;
  const group = groupId ?? first;
  if (!group) return trace.spans;
  return trace.spans.filter(span => (span.group ?? first) === group);
}

/* ---------- chart helpers ---------- */

/** Rows for a latency band plot; `event` marks steps where more than `fallbackEventPct` of chunks fell back. */
export function latencyRows(series: LatencySeries | null | undefined, fallbackEventPct = 10): Array<{ p50: number | null; p95: number | null; event: boolean }> {
  if (!series) return [];
  return series.p50.map((p50, i) => ({ p50, p95: series.p95[i] ?? null, event: (series.fallbackPct?.[i] ?? 0) > fallbackEventPct }));
}
/**
 * `count` evenly spaced short time labels ("10:00" … "Now") across a series of `length`
 * steps from `start`, on UTC or a device clock; the last label reads `lastLabel`.
 */
export function timeTicks(start: string, stepS: number, length: number, count = 5, lastLabel: string | null = "Now", utcOffsetMinutes = 0): string[] {
  const t0 = Date.parse(start);
  if (!Number.isFinite(t0) || length < 1 || count < 2) return [];
  return Array.from({ length: count }, (_, i) => {
    if (i === count - 1 && lastLabel) return lastLabel;
    const at = new Date(t0 + (i / (count - 1)) * (length - 1) * stepS * 1000 + utcOffsetMinutes * 60000);
    return at.toISOString().slice(11, 16);
  });
}
/** Log lines, newest first, for a robot and/or a configuration (a robot's lines count for its configuration). */
export function logsFor(ws: ConvoyWorkspace, filter: { robotId?: string; configId?: string } = {}, limit?: number): LogLine[] {
  const robotConfig = (line: LogLine) => line.configId ?? (line.robotId ? getRobot(ws, line.robotId)?.configId ?? null : null);
  const lines = ws.logs.filter(line => (!filter.robotId || line.robotId === filter.robotId) && (!filter.configId || robotConfig(line) === filter.configId)).toSorted(byTimeDesc);
  return limit === undefined ? lines : lines.slice(0, limit);
}
export function recentActivity(ws: ConvoyWorkspace, filter: { configId?: string } = {}, limit?: number): ActivityEvent[] {
  const events = ws.activity.filter(event => !filter.configId || event.configId === filter.configId).toSorted(byTimeDesc);
  return limit === undefined ? events : events.slice(0, limit);
}

/* ---------- readings, flags and health ---------- */

/** A flag in effect: stored (manual or prepared) or raised by a flag rule on measured telemetry. */
export interface ActiveFlag extends Omit<Flag, "provenance"> { provenance: Provenance; origin: "stored" | "rule" }

/** What a robot reports now: measured (live binding), stored sample/recorded values, or nothing. */
export interface RobotReadings {
  provenance: Provenance;
  /** True when the values come from a live device read. */
  live: boolean;
  latest: TelemetryReading | null;
  /** Recent samples per metric (measured series, or the stored recent sample series). */
  recent: Partial<Record<TelemetryMetric, SeriesPoint[]>>;
  /** Stored 24 h hourly series (sample robots only). */
  day: Partial<Record<TelemetryMetric, SeriesPoint[]>>;
  /** Edge planner latency: live gateway sample when bound, else the stored figures. */
  edgeMs: Percentiles | null;
  edgeProvenance: Provenance;
  cloudMs: Percentiles | null;
  fallbackPct: number | null;
  lastSeenAt: string | null;
  health: RobotHealth;
  healthReason: string | null;
  flags: ActiveFlag[];
}
const NOT_REPORTED: Provenance = { kind: "not-reported" };

function seriesSet(stored: Partial<Record<TelemetryMetric, Parameters<typeof seriesPoints>[0]>> | undefined): Partial<Record<TelemetryMetric, SeriesPoint[]>> {
  const out: Partial<Record<TelemetryMetric, SeriesPoint[]>> = {};
  for (const [metric, series] of Object.entries(stored ?? {}) as Array<[TelemetryMetric, Parameters<typeof seriesPoints>[0]]>) out[metric] = seriesPoints(series);
  return out;
}

/**
 * Evaluates a revision's flag rules on a reading (used for measured data; stored
 * robots carry their own flags). `thermalC` (the SoC software throttle point)
 * separates throttling from the attention rule; `now` enables the no-report rule.
 */
export function evaluateFlagRules(reading: TelemetryReading | null, rules: FlagRules | null, context: { capW?: number | null; thermalC?: number | null; fallbackPct?: number | null; edgeP95Ms?: number | null; lastSeenAt?: string | null; now?: number | null; provenance: Provenance; idPrefix: string }): ActiveFlag[] {
  if (!rules) return [];
  const flags: ActiveFlag[] = [];
  const at = reading?.at ?? context.lastSeenAt ?? new Date(0).toISOString();
  const add = (rule: Flag["rule"], severity: FlagSeverity, label: string, detail: string) => flags.push({ id: `${context.idPrefix}-${rule}`, rule, severity, label, detail, at, provenance: context.provenance, origin: "rule" });
  const temp = reading?.socTempC ?? null, throttle = context.thermalC ?? null;
  if (temp !== null && throttle !== null && temp >= throttle) add("soc-temp", "attention", "Thermal throttling", `Jetson SoC ${fmtFixed(temp)} °C, at or above the ${throttle} °C software throttle point`);
  else if (temp !== null && rules.socTempAttentionC !== null && temp >= rules.socTempAttentionC) add("soc-temp", "attention", "Near thermal throttle", `Jetson SoC ${fmtFixed(temp)} °C, at or above the ${rules.socTempAttentionC} °C attention rule${throttle !== null ? ` (software throttle at ${throttle} °C)` : ""}`);
  else if (temp !== null && rules.socTempWarnC !== null && temp >= rules.socTempWarnC) add("soc-temp", "warning", "Hot SoC", `Jetson SoC ${fmtFixed(temp)} °C, at or above the ${rules.socTempWarnC} °C warning rule`);
  const power = reading?.boardPowerPeakW ?? reading?.boardPowerW ?? null;
  if (power !== null && context.capW && rules.boardPowerWarnPctOfCap !== null && power >= context.capW * rules.boardPowerWarnPctOfCap / 100) add("board-power", "warning", "Power peaks", `Board input ${fmtFixed(power)} W against the ${context.capW} W mode`);
  const available = reading?.memAvailableMiB ?? null, total = reading?.memTotalMiB ?? null;
  if (available !== null && total && rules.memoryAvailableWarnPct !== null && available / total * 100 < rules.memoryAvailableWarnPct) add("memory", "warning", "Low memory", `${fmtPct(available / total * 100)} of memory available`);
  if (context.fallbackPct != null && rules.fallbackAttentionPct !== null && context.fallbackPct > rules.fallbackAttentionPct) add("fallback", "attention", "Cloud link degraded", `${fmtPct(context.fallbackPct)} of cloud chunks on fallback`);
  if (context.edgeP95Ms != null && rules.edgeP95BudgetMs !== null && context.edgeP95Ms > rules.edgeP95BudgetMs) add("edge-p95", "warning", "Edge latency over budget", `Edge p95 ${fmtMs(context.edgeP95Ms)} over the ${rules.edgeP95BudgetMs} ms budget`);
  if (context.lastSeenAt && context.now != null && rules.reportAttentionS !== null && (context.now - Date.parse(context.lastSeenAt)) / 1000 > rules.reportAttentionS) add("no-report", "attention", "No recent report", `No report for more than ${rules.reportAttentionS} s`);
  return flags;
}

function healthFrom(flags: readonly ActiveFlag[], fallback: RobotHealth): { health: RobotHealth; reason: string | null } {
  const attention = flags.find(flag => flag.severity === "attention"), warning = flags.find(flag => flag.severity === "warning");
  if (attention) return { health: "attention", reason: attention.label };
  if (warning) return { health: "degraded", reason: warning.label };
  return { health: fallback, reason: null };
}

/**
 * The robot's readings for display. A bound robot shows measured values from its
 * live binding (or "Not reported" while none have arrived), with health and
 * flags derived from those values and the revision's flag rules; other robots
 * show their stored sample or recorded values, health and flags.
 */
export function robotReadings(robot: Robot, revision: ConfigRevision | null, live?: LiveBinding | null, now?: number | null): RobotReadings {
  const stored = robot.flags.map(flag => ({ ...flag, origin: "stored" as const }));
  if (robot.deviceId) {
    const data = live?.data ?? null;
    const recordedLatency = robot.latency ?? null;
    if (!data) {
      return {
        provenance: NOT_REPORTED, live: false, latest: null, recent: {}, day: {},
        edgeMs: recordedLatency?.edgePlannerMs ?? null, edgeProvenance: recordedLatency?.provenance ?? NOT_REPORTED,
        cloudMs: null, fallbackPct: null, lastSeenAt: null, health: "not-reported",
        healthReason: live?.status === "loading" ? "Waiting for the first device report" : live?.error ?? "No live device data", flags: stored,
      };
    }
    const mode = revision?.edgeHardware.powerModes.find(item => item.id === revision.edgeHardware.powerModeId);
    const ruleFlags = evaluateFlagRules(data.latest, revision?.flagRules ?? null, {
      capW: mode?.capW ?? null, thermalC: revision?.edgeHardware.thermal.swThrottleC ?? null, edgeP95Ms: data.edgeLatency?.p95Ms ?? null, lastSeenAt: data.liveAt, now, provenance: data.provenance, idPrefix: robot.id,
    });
    const flags = [...stored, ...ruleFlags.filter(flag => !stored.some(item => item.rule === flag.rule))];
    const offline = !data.online;
    const derived = healthFrom(flags, data.observedHealth && data.observedHealth !== "ok" && data.observedHealth !== "healthy" ? "degraded" : "healthy");
    return {
      provenance: data.provenance, live: true, latest: data.latest, recent: { ...data.series }, day: {},
      edgeMs: data.edgeLatency ? { p50: data.edgeLatency.p50Ms, p95: data.edgeLatency.p95Ms, n: data.edgeLatency.n } : recordedLatency?.edgePlannerMs ?? null,
      edgeProvenance: data.edgeLatency ? { kind: "measured", at: data.edgeLatency.to ?? data.fetchedAt, n: data.edgeLatency.n, source: data.name } : recordedLatency?.provenance ?? NOT_REPORTED,
      cloudMs: null, fallbackPct: null, lastSeenAt: data.liveAt,
      health: offline ? "offline" : derived.health,
      healthReason: offline ? data.identityState ?? "No recent live contact" : derived.reason,
      flags,
    };
  }
  const telemetry = robot.telemetry;
  return {
    provenance: telemetry?.provenance ?? NOT_REPORTED, live: false, latest: telemetry?.latest ?? null,
    recent: seriesSet(telemetry?.recent), day: seriesSet(telemetry?.day),
    edgeMs: robot.latency?.edgePlannerMs ?? null, edgeProvenance: robot.latency?.provenance ?? NOT_REPORTED,
    cloudMs: robot.latency?.cloudChunkMs ?? null, fallbackPct: robot.latency?.fallbackPct ?? null,
    lastSeenAt: robot.lastSeenAt ?? telemetry?.latest.at ?? null,
    health: robot.health, healthReason: robot.healthReason ?? null, flags: stored,
  };
}

const severityRank = (flags: readonly ActiveFlag[]) => flags.some(flag => flag.severity === "attention") ? 2 : flags.length ? 1 : 0;
export interface FlaggedRobot { robot: Robot; flags: ActiveFlag[]; severity: FlagSeverity }
/** Robots with a flag in effect (attention first), for a configuration or the whole workspace. */
export function flaggedRobots(ws: ConvoyWorkspace, configId?: string | null, live: LiveMap = {}, now?: number | null): FlaggedRobot[] {
  return ws.robots.filter(robot => robot.configId !== null && (!configId || robot.configId === configId)).map(robot => {
    const config = robot.configId ? getConfiguration(ws, robot.configId) : null;
    const readings = robotReadings(robot, config ? getRevision(config, robot.rev) : null, live[robot.id], now);
    return { robot, flags: readings.flags, severity: severityRank(readings.flags) === 2 ? "attention" as const : "warning" as const };
  }).filter(entry => entry.flags.length).toSorted((a, b) => severityRank(b.flags) - severityRank(a.flags) || collator.compare(a.robot.name, b.robot.name));
}

/* ---------- summaries ---------- */

export interface ConfigurationSummary {
  robots: { test: number; production: number; total: number };
  attention: number;
  degraded: number;
  /** Newest gated suite run for the revision under test, else for any revision. */
  latestRun: EvalRun | null;
  /** Newest run of any kind still running or queued. */
  activeRun: EvalRun | null;
  revision: ConfigRevision;
}
export function configurationSummary(ws: ConvoyWorkspace, config: Configuration, live: LiveMap = {}, now?: number | null): ConfigurationSummary {
  const robots = robotsFor(ws, config.id);
  const health = robots.map(robot => robotReadings(robot, getRevision(config, robot.rev), live[robot.id], now).health);
  return {
    robots: { test: robots.filter(robot => robot.role === "test").length, production: robots.filter(robot => robot.role === "production").length, total: robots.length },
    attention: health.filter(item => item === "attention").length,
    degraded: health.filter(item => item === "degraded").length,
    latestRun: latestGateRun(ws, config.id, config.candidateRev) ?? latestGateRun(ws, config.id),
    activeRun: runsFor(ws, { configId: config.id }).find(run => run.status === "running" || run.status === "queued") ?? null,
    revision: currentRevision(config),
  };
}
