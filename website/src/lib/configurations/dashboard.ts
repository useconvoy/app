/**
 * Configuration dashboard model (`/app/configurations/[configId]`): one row per
 * attached robot with its readings, the robots table filters and sort, the
 * production KPI aggregation, shared chart domains and ticks, the promotion gate
 * of a revision, the attention banner and the log filters. Pure functions; the
 * page renders them (src/components/configurations/pages/ConfigDashboardPage.tsx).
 *
 * Evidence rules kept here:
 * - A robot with a live device binding is never aggregated with stored robots. Its
 *   values are measured while it reports and missing (null) while it is offline or
 *   unavailable; the recorded figures a bound robot may store are never shown as
 *   its current values.
 * - An aggregate carries the weakest provenance of its inputs (sample below
 *   recorded) and states its basis: the stored production summary, or the robots.
 * - A missing value stays null ("Not reported"), never 0.
 */
import { fmtDate, fmtTime, median } from "./format";
import { routes } from "./routes";
import { getRevision, getSuite, latestGateRun, resolveRobotRoute, robotReadings, robotsFor, timeTicks } from "./selectors";
import type { ActiveFlag, FlaggedRobot, LiveMap, RobotReadings } from "./selectors";
import type {
  ConfigRevision, Configuration, ConvoyWorkspace, EvalRun, LatencySeries, LogLevel, LogLine, Percentiles, ProductionSummary, Provenance, Robot, RobotHealth,
  TelemetryReading,
} from "./types";

const NOT_REPORTED: Provenance = { kind: "not-reported" };
const collator = new Intl.Collator("en-US", { numeric: true, sensitivity: "base" });
const finite = (value: number | null | undefined): value is number => typeof value === "number" && Number.isFinite(value);

/* ---------- provenance of an aggregate ---------- */

/**
 * The weakest provenance among stored inputs: "sample" if any input is sample,
 * otherwise "recorded" spanning the inputs' dates (n summed when every input has
 * one). Not-reported inputs are skipped; none left is "not-reported". Callers pass
 * stored (non-measured) provenance only: measured values are never aggregated.
 */
export function weakestProvenance(list: ReadonlyArray<Provenance | null | undefined>): Provenance {
  const stored = list.filter((item): item is Provenance => !!item && (item.kind === "sample" || item.kind === "recorded"));
  if (!stored.length) return NOT_REPORTED;
  if (stored.some(item => item.kind === "sample")) return { kind: "sample" };
  const starts = stored.map(item => item.at).filter((at): at is string => !!at && Number.isFinite(Date.parse(at))).toSorted();
  const ends = stored.map(item => item.until ?? item.at).filter((at): at is string => !!at && Number.isFinite(Date.parse(at))).toSorted();
  const sources = new Set(stored.map(item => item.source ?? ""));
  const n = stored.every(item => finite(item.n)) ? stored.reduce((sum, item) => sum + (item.n ?? 0), 0) : undefined;
  return {
    kind: "recorded",
    ...(starts[0] ? { at: starts[0] } : {}),
    ...(ends.length && ends.at(-1) !== starts[0] ? { until: ends.at(-1) } : {}),
    ...(n !== undefined ? { n } : {}),
    ...(sources.size === 1 && [...sources][0] ? { source: [...sources][0] } : {}),
  };
}

/* ---------- robot rows ---------- */

export interface RobotRow {
  robot: Robot;
  revision: ConfigRevision | null;
  readings: RobotReadings;
  /** Live device binding: values are measured while it reports, never stored or aggregated. */
  bound: boolean;
  /** Has a current report: a bound robot that reads and is not offline; a stored robot with a latest reading that is not offline. */
  reporting: boolean;
  /** The reading to show as current (a bound robot's only while it reports). */
  current: TelemetryReading | null;
  /** Edge planner latency to show: a bound robot's measured gateway sample only. */
  edge: { ms: Percentiles | null; provenance: Provenance };
  /** Robot page, when the robot resolves under this configuration. */
  href: string | null;
}

/** Rows for the robots attached to a configuration, in `robotsFor` order (bound first, test before production, by name). */
export function robotRows(ws: ConvoyWorkspace, config: Configuration, live: LiveMap = {}, now?: number | null): RobotRow[] {
  return robotsFor(ws, config.id).map(robot => {
    const revision = getRevision(config, robot.rev);
    const readings = robotReadings(robot, revision, live[robot.id], now);
    const bound = !!robot.deviceId;
    const reporting = bound
      ? readings.live && readings.health !== "offline"
      : readings.latest !== null && readings.health !== "offline" && readings.health !== "not-reported";
    const edge = !bound ? { ms: readings.edgeMs, provenance: readings.edgeProvenance }
      : reporting && readings.edgeProvenance.kind === "measured" ? { ms: readings.edgeMs, provenance: readings.edgeProvenance }
        : { ms: null, provenance: NOT_REPORTED };
    return {
      robot, revision, readings, bound, reporting, edge,
      current: bound && !reporting ? null : readings.latest,
      href: resolveRobotRoute(ws, config.id, robot.id) ? routes.robot(config.id, robot.id) : null,
    };
  });
}

/** A row with nothing to show in its metric columns (no reading and no latency). */
export function isSilent(row: RobotRow): boolean {
  return row.current === null && row.edge.ms === null && row.readings.cloudMs === null && row.readings.fallbackPct === null;
}

/* ---------- filters and sort ---------- */

export type RobotFilter = "all" | "test" | "production" | "attention";
export const ROBOT_FILTERS: readonly RobotFilter[] = ["all", "test", "production", "attention"];
/** A query value as a filter; unknown values read as "all". */
export function parseRobotFilter(value: string | null | undefined): RobotFilter {
  return ROBOT_FILTERS.includes(value as RobotFilter) ? value as RobotFilter : "all";
}
/**
 * Needs attention: the robot's health says so, or a flag in effect has that
 * severity (a manual flag does not change a stored robot's health).
 */
export function needsAttention(row: RobotRow): boolean {
  return row.readings.health === "attention" || row.readings.flags.some(flag => flag.severity === "attention");
}
/** "attention" keeps robots that need attention (a warning alone is Degraded). */
export function matchesRobotFilter(row: RobotRow, filter: RobotFilter): boolean {
  return filter === "all" || (filter === "attention" ? needsAttention(row) : row.robot.role === filter);
}
export function filterRobotRows(rows: readonly RobotRow[], filter: RobotFilter): RobotRow[] {
  return rows.filter(row => matchesRobotFilter(row, filter));
}
export function robotFilterCounts(rows: readonly RobotRow[]): Record<RobotFilter, number> {
  const count = (filter: RobotFilter) => filterRobotRows(rows, filter).length;
  return { all: count("all"), test: count("test"), production: count("production"), attention: count("attention") };
}

export type RobotSortKey = "name" | "health" | "temp" | "power" | "planner" | "fallback";
export interface RobotSort { key: RobotSortKey; dir: "asc" | "desc" }
/** Direction of a column's first click: names A–Z, everything else worst or highest first. */
export const ROBOT_SORT_FIRST: Record<RobotSortKey, RobotSort["dir"]> = { name: "asc", health: "desc", temp: "desc", power: "desc", planner: "desc", fallback: "desc" };
export const DEFAULT_ROBOT_SORT: RobotSort = { key: "health", dir: "desc" };
/** Triage order: Needs attention, Degraded, Healthy, then Offline and Not reported. */
export const HEALTH_RANK: Record<RobotHealth, number> = { attention: 3, degraded: 2, healthy: 1, offline: 0, "not-reported": 0 };
/** Health rank raised by the flags in effect: an attention flag ranks as Needs attention, a warning as Degraded. */
export function triageRank(row: RobotRow): number {
  if (needsAttention(row)) return HEALTH_RANK.attention;
  return Math.max(HEALTH_RANK[row.readings.health], row.readings.flags.length ? HEALTH_RANK.degraded : 0);
}

export function robotSortValue(row: RobotRow, key: RobotSortKey): string | number | null {
  switch (key) {
    case "name": return row.robot.name;
    case "health": return triageRank(row);
    case "temp": return row.current?.socTempC ?? null;
    case "power": return row.current?.boardPowerW ?? null;
    case "planner": return row.edge.ms?.p50 ?? null;
    case "fallback": return row.readings.fallbackPct;
  }
}
/** The next sort after clicking a column header: the same column flips, another starts at its first direction. */
export function nextRobotSort(current: RobotSort, key: RobotSortKey): RobotSort {
  return current.key === key ? { key, dir: current.dir === "asc" ? "desc" : "asc" } : { key, dir: ROBOT_SORT_FIRST[key] };
}
/**
 * Sorted rows: live-bound robots stay pinned first; missing values sort last in
 * either direction; ties keep name order.
 */
export function sortRobotRows(rows: readonly RobotRow[], sort: RobotSort): RobotRow[] {
  const direction = sort.dir === "asc" ? 1 : -1;
  return rows.toSorted((a, b) => {
    const pin = Number(b.bound) - Number(a.bound);
    if (pin) return pin;
    const va = robotSortValue(a, sort.key), vb = robotSortValue(b, sort.key);
    if (va === null || vb === null) {
      if (va !== vb) return va === null ? 1 : -1;
    } else {
      const order = typeof va === "number" && typeof vb === "number" ? va - vb : collator.compare(String(va), String(vb));
      if (order) return order * direction;
    }
    return collator.compare(a.robot.name, b.robot.name);
  });
}
/** Sort value for a table column that keeps missing values last in the given direction (for `DataTable` columns). */
export function robotSortAccessor(key: RobotSortKey, dir: RobotSort["dir"]): (row: RobotRow) => string | number {
  return row => robotSortValue(row, key) ?? (dir === "desc" ? Number.NEGATIVE_INFINITY : Number.POSITIVE_INFINITY);
}

/* ---------- production KPIs ---------- */

/** Where an aggregate comes from: the configuration's stored production summary, or the attached production robots. */
export type KpiBasis = "summary" | "robots";
export interface LatencyKpi {
  basis: KpiBasis;
  /** Summary: the pooled p50. Robots: the median of the robots' p50 values. */
  p50: number | null;
  /** Summary only: the pooled p95 (a p95 cannot be pooled from per-robot figures). */
  p95: number | null;
  /** Robots only: the range of the robots' p95 values. */
  p95Range: [number, number] | null;
  /** Robots behind the figure. */
  n: number;
  provenance: Provenance;
}
export interface ProductionKpis {
  /** Production robots aggregated (stored values only). */
  robots: RobotRow[];
  /** Live-bound production robots: shown on their own, never part of an aggregate. */
  excluded: RobotRow[];
  summary: ProductionSummary | null;
  /** Window label, e.g. "24 h". */
  window: string | null;
  /** Provenance of the KPI strip as a whole. */
  provenance: Provenance;
  reporting: { count: number; of: number; provenance: Provenance };
  edge: LatencyKpi | null;
  cloud: LatencyKpi | null;
  fallback: { pct: number; basis: KpiBasis; n: number; provenance: Provenance } | null;
  interventions: { mean: number | null; min: number | null; max: number | null; basis: KpiBasis; n: number; provenance: Provenance } | null;
  safety: { critical: number; major: number; minor: number; note?: string; provenance: Provenance } | null;
}

function robotLatency(entries: ReadonlyArray<{ ms: Percentiles | null; provenance: Provenance }>): LatencyKpi | null {
  const usable = entries.filter(entry => entry.ms && finite(entry.ms.p50) && (entry.provenance.kind === "sample" || entry.provenance.kind === "recorded"));
  if (!usable.length) return null;
  const p95s = usable.map(entry => entry.ms?.p95).filter(finite);
  return {
    basis: "robots", p50: median(usable.map(entry => entry.ms?.p50)), p95: null,
    p95Range: p95s.length ? [Math.min(...p95s), Math.max(...p95s)] : null, n: usable.length, provenance: weakestProvenance(usable.map(entry => entry.provenance)),
  };
}

/**
 * The production KPI strip over a configuration's production robots, excluding
 * live-bound robots. Counts (reporting) come from the robots; pooled statistics
 * (percentiles, fallback share, interventions, safety events) come from the stored
 * production summary when there is one, else from the robots with their basis
 * stated (median of per-robot p50 values, mean of per-robot rates).
 */
export function productionKpis(config: Configuration, rows: readonly RobotRow[]): ProductionKpis {
  const production = rows.filter(row => row.robot.role === "production");
  const robots = production.filter(row => !row.bound), excluded = production.filter(row => row.bound);
  const summary = config.production ?? null;
  const reporting = { count: robots.filter(row => row.reporting).length, of: robots.length, provenance: weakestProvenance(robots.map(row => row.readings.provenance)) };
  const pooled = (ms: Percentiles | null): LatencyKpi | null => summary && ms
    ? { basis: "summary", p50: ms.p50, p95: ms.p95, p95Range: null, n: summary.robots, provenance: summary.provenance } : null;

  const edge = summary ? pooled(summary.edgePlannerMs) : robotLatency(robots.map(row => ({ ms: row.readings.edgeMs, provenance: row.readings.edgeProvenance })));
  const cloud = summary ? pooled(summary.cloudChunkMs) : robotLatency(robots.map(row => ({ ms: row.readings.cloudMs, provenance: row.readings.edgeProvenance })));

  let fallback: ProductionKpis["fallback"] = null;
  if (summary) {
    if (finite(summary.fallbackPct)) fallback = { pct: summary.fallbackPct, basis: "summary", n: summary.robots, provenance: summary.provenance };
  } else {
    const shares = robots.filter(row => finite(row.readings.fallbackPct));
    if (shares.length) fallback = { pct: shares.reduce((sum, row) => sum + (row.readings.fallbackPct ?? 0), 0) / shares.length, basis: "robots", n: shares.length, provenance: weakestProvenance(shares.map(row => row.readings.edgeProvenance)) };
  }

  let interventions: ProductionKpis["interventions"] = null;
  if (summary) {
    if (summary.interventionsPerRobotHour) interventions = { ...summary.interventionsPerRobotHour, basis: "summary", n: summary.robots, provenance: summary.provenance };
  } else {
    const rates = robots.map(row => row.robot.interventions).filter((item): item is NonNullable<Robot["interventions"]> => !!item && finite(item.perHour) && item.provenance.kind !== "not-reported");
    const values = rates.map(item => item.perHour as number);
    if (values.length) interventions = { mean: values.reduce((sum, value) => sum + value, 0) / values.length, min: Math.min(...values), max: Math.max(...values), basis: "robots", n: values.length, provenance: weakestProvenance(rates.map(item => item.provenance)) };
  }

  const windows = new Set(robots.map(row => row.robot.latency?.window).filter((window): window is string => !!window));
  return {
    robots, excluded, summary,
    window: summary?.window.label ?? (windows.size === 1 ? [...windows][0] : null),
    provenance: summary?.provenance ?? reporting.provenance,
    reporting, edge, cloud, fallback, interventions,
    safety: summary?.safetyEvents ? { ...summary.safetyEvents, provenance: summary.provenance } : null,
  };
}

/* ---------- chart domains and ticks ---------- */

/**
 * One y-domain for a group of small multiples, so reference lines sit at the same
 * height on every card: the values and reference lines padded by one `step` and
 * rounded out to it; `zero` starts the axis at 0 (power).
 */
export function telemetryDomain(values: ReadonlyArray<number | null | undefined>, references: ReadonlyArray<number | null | undefined> = [], options: { step?: number; zero?: boolean } = {}): [number, number] | null {
  const step = options.step ?? 5;
  const all = [...values, ...references].filter(finite);
  if (!all.length) return null;
  const low = Math.min(...all), high = Math.max(...all);
  const lo = options.zero ? 0 : Math.floor((low - step) / step) * step;
  const hi = Math.max(Math.ceil((high + step) / step) * step, lo + step);
  return [lo, hi];
}
/** y of a value in the sparkline geometry (viewBox 0 0 300 80: lo → 66, hi → 14), clamped to the domain. */
export function sparkY(value: number, domain: readonly [number, number]): number {
  const [lo, hi] = domain;
  return Math.round((66 - (Math.min(Math.max(value, lo), hi) - lo) / ((hi - lo) || 1) * 52) * 100) / 100;
}
/** Dashed reference lines across a sparkline for each finite value. */
export function sparkReferences(values: ReadonlyArray<number | null | undefined>, domain: readonly [number, number]): string {
  return values.filter(finite).map(value => `M12,${sparkY(value, domain)}H288`).join("");
}
/** The band between two reference values (e.g. the thermal guard and the throttle point), or "" when either is missing. */
export function sparkZone(from: number | null | undefined, to: number | null | undefined, domain: readonly [number, number]): string {
  if (!finite(from) || !finite(to) || from === to) return "";
  const top = sparkY(Math.max(from, to), domain), bottom = sparkY(Math.min(from, to), domain);
  return `M12,${top}H288V${bottom}H12Z`;
}

/** Ticks from 0 for a latency plot: a 1 / 2 / 2.5 / 5 step giving at most `intervals` intervals up to `max`. */
export function niceTicks(max: number, intervals = 4): number[] {
  if (!(max > 0)) return [0, 1];
  const raw = max / intervals;
  const power = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map(multiple => multiple * power).find(candidate => candidate >= raw - 1e-9) ?? 10 * power;
  const count = Math.max(1, Math.ceil(max / step - 1e-9));
  return Array.from({ length: count + 1 }, (_, i) => Math.round(i * step * 1000) / 1000);
}

/** Time of the last step of a latency series. */
export function seriesEnd(series: Pick<LatencySeries, "start" | "stepS" | "p50">): number {
  return Date.parse(series.start) + Math.max(0, series.p50.length - 1) * series.stepS * 1000;
}
/** x labels for a latency series; the last reads "Now" only when the series ends within two steps of `now`. */
export function latencyTicks(series: LatencySeries, now: number | null | undefined, count = 5): string[] {
  const end = seriesEnd(series);
  const recent = finite(now) && Number.isFinite(end) && Math.abs(now - end) <= 2 * series.stepS * 1000;
  return timeTicks(series.start, series.stepS, series.p50.length, count, recent ? "Now" : null);
}
/** "Sep 30 10:00 – Oct 1 10:00 UTC". */
export function seriesWindowLabel(series: Pick<LatencySeries, "start" | "stepS" | "p50">): string {
  const end = new Date(seriesEnd(series)).toISOString();
  return `${fmtDate(series.start)} ${fmtTime(series.start, undefined, false)} – ${fmtDate(end)} ${fmtTime(end, undefined, false)} UTC`;
}
/** "hourly", "every 15 min", "every 30 s". */
export function cadenceLabel(stepS: number): string {
  if (stepS === 3600) return "hourly";
  if (stepS % 3600 === 0) return `every ${stepS / 3600} h`;
  if (stepS % 60 === 0) return stepS === 60 ? "per minute" : `every ${stepS / 60} min`;
  return `every ${stepS} s`;
}

/* ---------- promotion gate ---------- */

export interface GateLine { label: string; target: string | null; actual: string; passed: boolean }
export interface RevisionGate {
  rev: string;
  /** Newest finished suite run with a gate decision for this revision. */
  run: EvalRun | null;
  passed: boolean;
  /** Criteria as stated by the run (with the suite's labels and targets). */
  lines: GateLine[];
  /** Plain sentence: "r4 passed gate on Run 23", "r2 is below gate on Run 21 (…)", "r1 has no gated suite run yet". */
  reason: string;
}
/** Whether a revision may go to production robots: its latest gated suite run must have passed. */
export function revisionGate(ws: ConvoyWorkspace, config: Configuration, rev: string): RevisionGate {
  const run = latestGateRun(ws, config.id, rev);
  if (!run?.gate) return { rev, run: null, passed: false, lines: [], reason: `${rev} has no gated suite run yet` };
  const suite = getSuite(ws, run.suiteId);
  const lines = run.gate.results.map(result => {
    const criterion = suite?.gate.find(item => item.id === result.criterionId);
    return { label: criterion?.label ?? result.criterionId, target: criterion?.target ?? null, actual: result.actual, passed: result.passed };
  });
  if (run.gate.passed) return { rev, run, passed: true, lines, reason: `${rev} passed gate on Run ${run.number}` };
  const failing = lines.filter(line => !line.passed).map(line => `${line.label}: ${line.actual}${line.target ? `, needs ${line.target}` : ""}`);
  return { rev, run, passed: false, lines, reason: `${rev} is below gate on Run ${run.number}${failing.length ? ` (${failing.join("; ")})` : ""}` };
}

/* ---------- attention banner ---------- */

export interface AttentionLine { robot: Robot; flag: ActiveFlag; /** Further flags on the same robot. */ more: number }
/** Flagged robots split into "needs attention" and "warning" lines, each with the robot's leading flag. */
export function attentionSummary(flagged: readonly FlaggedRobot[]): { attention: AttentionLine[]; warning: AttentionLine[] } {
  const line = (entry: FlaggedRobot): AttentionLine => ({ robot: entry.robot, flag: entry.flags.find(flag => flag.severity === entry.severity) ?? entry.flags[0], more: entry.flags.length - 1 });
  return { attention: flagged.filter(entry => entry.severity === "attention").map(line), warning: flagged.filter(entry => entry.severity === "warning").map(line) };
}

/* ---------- logs ---------- */

export type LogLevelFilter = "all" | LogLevel;
export interface LogFilter { level: LogLevelFilter; source: string | null; robotId: string | null }
export const ALL_LOGS: LogFilter = { level: "all", source: null, robotId: null };
export function filterLogs(lines: readonly LogLine[], filter: LogFilter): LogLine[] {
  return lines.filter(line => (filter.level === "all" || line.level === filter.level) && (!filter.source || line.source === filter.source) && (!filter.robotId || line.robotId === filter.robotId));
}
/** Counts per level and the sources and robots present, for the log filters. */
export function logFacets(lines: readonly LogLine[]): { levels: Record<LogLevelFilter, number>; sources: string[]; robotIds: string[] } {
  const count = (level: LogLevelFilter) => filterLogs(lines, { ...ALL_LOGS, level }).length;
  return {
    levels: { all: lines.length, info: count("info"), warn: count("warn"), error: count("error") },
    sources: [...new Set(lines.map(line => line.source))].toSorted(collator.compare),
    robotIds: [...new Set(lines.map(line => line.robotId).filter((id): id is string => !!id))],
  };
}
