/**
 * Pure read helpers over a `ConvoyWorkspace` (re-exported from client.ts).
 * Live bindings are passed in (`LiveBinding`, keyed by robot id) so measured
 * values replace stored ones for bound robots and never mix with them.
 */
import { fmtFixed, fmtMs, fmtPct, seriesPoints } from "./format";
import type { LiveBinding } from "./live";
import type {
  ActionTrace, ConfigRevision, Configuration, ConvoyWorkspace, EvalSuite, Flag, FlagRules,
  FlagSeverity, Percentiles, Provenance, Robot, RobotHealth, SeriesPoint, TelemetryMetric, TelemetryReading,
} from "./types";

export type LiveMap = Readonly<Record<string, LiveBinding | undefined>>;
const byTimeDesc = <T extends { at: string }>(a: T, b: T) => Date.parse(b.at) - Date.parse(a.at);
const collator = new Intl.Collator("en-US", { numeric: true, sensitivity: "base" });

/* ---------- configurations ---------- */

/** Configurations in the order they were created (oldest first). */
export function listConfigurations(ws: ConvoyWorkspace): Configuration[] {
  return ws.configurations.toSorted((a, b) => Date.parse(a.createdAt) - Date.parse(b.createdAt));
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

/* ---------- robots ---------- */

/** Robots attached to a configuration: live-bound first, then by name. */
export function robotsFor(ws: ConvoyWorkspace, configId: string): Robot[] {
  return ws.robots.filter(robot => robot.configId === configId)
    .toSorted((a, b) => Number(!!b.deviceId) - Number(!!a.deviceId) || collator.compare(a.name, b.name));
}
export function getRobot(ws: ConvoyWorkspace, robotId: string): Robot | null {
  return ws.robots.find(robot => robot.id === robotId) ?? null;
}
export function resolveRobotRoute(ws: ConvoyWorkspace, configId: string, robotId: string): { configuration: Configuration; robot: Robot } | null {
  const configuration = getConfiguration(ws, configId), robot = getRobot(ws, robotId);
  return configuration && robot && robot.configId === configId ? { configuration, robot } : null;
}

/* ---------- evaluations ---------- */

export function getSuite(ws: ConvoyWorkspace, suiteId: string | null | undefined): EvalSuite | null {
  return suiteId ? ws.suites.find(suite => suite.id === suiteId) ?? null : null;
}

/* ---------- traces ---------- */

/** Stored action traces for a robot, newest first. */
export function tracesFor(ws: ConvoyWorkspace, robotId: string): ActionTrace[] {
  return ws.traces.filter(trace => trace.robotId === robotId).toSorted(byTimeDesc);
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
  /** The health every page shows: `baseHealth` raised by the flags in effect (`displayHealth`). */
  health: RobotHealth;
  /** One line for `health`: the flag that set it, else the base reason. */
  healthReason: string | null;
  /** The flag behind `health`, when a flag set it. */
  healthFlag: ActiveFlag | null;
  /** Health before flags: the stored health, or a bound robot's device state (offline, not reported, or the device's own health). */
  baseHealth: RobotHealth;
  baseHealthReason: string | null;
  flags: ActiveFlag[];
}
const NOT_REPORTED: Provenance = { kind: "not-reported" };

/* ---------- displayed health (one rule for every page) ---------- */

export interface HealthState { health: RobotHealth; reason: string | null }
export interface DisplayedHealth extends HealthState { /** The flag that set the health, if one did. */ flag: ActiveFlag | null }

/**
 * The health every page shows for a robot (robot page, dashboard table and
 * device board, attention banner, index counts): its base health — the stored
 * health, or a bound robot's device state — raised by the flags in effect.
 * Precedence, most severe first:
 * 1. an attention flag, or attention health → Needs attention;
 * 2. Offline (a warning on an offline robot does not hide that it is offline);
 * 3. a warning flag, or degraded health → Degraded;
 * 4. the base health (Healthy, Not reported).
 * Needs attention outranks Offline, so a robot flagged for not reporting is
 * triaged with the others. The reason is the flag that set the health, else the
 * base reason.
 */
export function displayHealth(base: HealthState, flags: readonly ActiveFlag[]): DisplayedHealth {
  const attention = flags.find(flag => flag.severity === "attention");
  if (attention) return { health: "attention", reason: attention.label, flag: attention };
  if (base.health === "attention" || base.health === "offline") return { ...base, flag: null };
  const warning = flags.find(flag => flag.severity === "warning");
  if (warning) return { health: "degraded", reason: base.health === "degraded" ? base.reason ?? warning.label : warning.label, flag: warning };
  return { ...base, flag: null };
}

/** Triage severity of a displayed health: Needs attention → "attention", Degraded → "warning", anything else none. */
export function healthSeverity(health: RobotHealth): FlagSeverity | null {
  return health === "attention" ? "attention" : health === "degraded" ? "warning" : null;
}

function withHealth(base: HealthState, flags: ActiveFlag[]): Pick<RobotReadings, "health" | "healthReason" | "healthFlag" | "baseHealth" | "baseHealthReason" | "flags"> {
  const shown = displayHealth(base, flags);
  return { health: shown.health, healthReason: shown.reason, healthFlag: shown.flag, baseHealth: base.health, baseHealthReason: base.reason, flags };
}

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

/**
 * A bound robot's device state before flags: offline (or retired, revoked, never
 * seen), else the device's own health — Healthy for "ok", Not reported when the
 * agent reports none ("unknown", e.g. no release running), Degraded otherwise
 * ("failed"). An unknown health is not evidence of a problem, so it raises no warning.
 */
function deviceHealth(data: NonNullable<LiveBinding["data"]>): HealthState {
  if (!data.online) return { health: "offline", reason: data.identityState ?? "No recent live contact" };
  const observed = data.observedHealth;
  if (observed === "ok" || observed === "healthy") return { health: "healthy", reason: null };
  if (!observed || observed === "unknown") return { health: "not-reported", reason: observed ? "Device health: unknown" : "Device health not reported" };
  return { health: "degraded", reason: `Device health: ${observed}` };
}

/**
 * The robot's readings for display. A bound robot shows measured values from its
 * live binding (or "Not reported" while none have arrived), with flags raised by
 * the revision's flag rules on those values; other robots show their stored
 * sample or recorded values and flags. `health` is the displayed health
 * (`displayHealth`) every page uses; `baseHealth` is the state before flags.
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
        cloudMs: null, fallbackPct: null, lastSeenAt: null,
        ...withHealth({ health: "not-reported", reason: live?.status === "loading" ? "Waiting for the first device report" : live?.error ?? "No live device data" }, stored),
      };
    }
    const mode = revision?.edgeHardware.powerModes.find(item => item.id === revision.edgeHardware.powerModeId);
    // "No recent report" compares server timestamps on the server's clock: the browser's clock plus the read's offset.
    const serverNow = now == null ? null : now + (data.clockOffsetMs ?? 0);
    const ruleFlags = evaluateFlagRules(data.latest, revision?.flagRules ?? null, {
      capW: mode?.capW ?? null, thermalC: revision?.edgeHardware.thermal.swThrottleC ?? null, edgeP95Ms: data.edgeLatency?.p95Ms ?? null, lastSeenAt: data.liveAt, now: serverNow, provenance: data.provenance, idPrefix: robot.id,
    });
    const flags = [...stored, ...ruleFlags.filter(flag => !stored.some(item => item.rule === flag.rule))];
    return {
      provenance: data.provenance, live: true, latest: data.latest, recent: { ...data.series }, day: {},
      edgeMs: data.edgeLatency ? { p50: data.edgeLatency.p50Ms, p95: data.edgeLatency.p95Ms, n: data.edgeLatency.n } : recordedLatency?.edgePlannerMs ?? null,
      edgeProvenance: data.edgeLatency ? { kind: "measured", at: data.edgeLatency.to ?? data.fetchedAt, n: data.edgeLatency.n, source: data.name } : recordedLatency?.provenance ?? NOT_REPORTED,
      cloudMs: null, fallbackPct: null, lastSeenAt: data.liveAt,
      ...withHealth(deviceHealth(data), flags),
    };
  }
  const telemetry = robot.telemetry;
  return {
    provenance: telemetry?.provenance ?? NOT_REPORTED, live: false, latest: telemetry?.latest ?? null,
    recent: seriesSet(telemetry?.recent), day: seriesSet(telemetry?.day),
    edgeMs: robot.latency?.edgePlannerMs ?? null, edgeProvenance: robot.latency?.provenance ?? NOT_REPORTED,
    cloudMs: robot.latency?.cloudChunkMs ?? null, fallbackPct: robot.latency?.fallbackPct ?? null,
    lastSeenAt: robot.lastSeenAt ?? telemetry?.latest.at ?? null,
    ...withHealth({ health: robot.health, reason: robot.healthReason ?? null }, stored),
  };
}

/** A robot whose displayed health is Needs attention or Degraded, with what to say about it. */
export interface AttentionRobot {
  robot: Robot;
  /** "attention" for Needs attention, "warning" for Degraded. */
  severity: FlagSeverity;
  readings: RobotReadings;
  /** Every flag in effect on the robot. */
  flags: ActiveFlag[];
  /** Short reason: the flag that set the health, else the robot's health reason. */
  label: string;
  /** Evidence sentence: the flag's detail, else the health reason. */
  detail: string;
  /** Provenance of that evidence. */
  provenance: Provenance;
}
const HEALTH_TEXT: Partial<Record<RobotHealth, string>> = { attention: "Needs attention", degraded: "Degraded" };
const severityOrder = (entry: AttentionRobot) => entry.severity === "attention" ? 0 : 1;

/**
 * Robots that need attention (displayed health Needs attention) or carry a
 * warning (Degraded), attention first, then by name, for one configuration or
 * every attached robot. The attention banner and the index counts both read
 * this, so they always agree with the health badges.
 */
export function attentionRobots(ws: ConvoyWorkspace, configId?: string | null, live: LiveMap = {}, now?: number | null): AttentionRobot[] {
  const entries: AttentionRobot[] = [];
  for (const robot of ws.robots) {
    if (robot.configId === null || (configId && robot.configId !== configId)) continue;
    const config = getConfiguration(ws, robot.configId);
    const readings = robotReadings(robot, config ? getRevision(config, robot.rev) : null, live[robot.id], now);
    const severity = healthSeverity(readings.health);
    if (!severity) continue;
    const flag = readings.healthFlag;
    const label = flag?.label ?? readings.healthReason ?? HEALTH_TEXT[readings.health] ?? readings.health;
    entries.push({ robot, severity, readings, flags: readings.flags, label, detail: flag?.detail ?? label, provenance: flag?.provenance ?? readings.provenance });
  }
  return entries.toSorted((a, b) => severityOrder(a) - severityOrder(b) || collator.compare(a.robot.name, b.robot.name));
}
