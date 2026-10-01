/**
 * Robot dashboard helpers (screen 3), pure functions over the workspace and the
 * live device data:
 * - statistics of the received on-device gateway spans (median and nearest-rank
 *   p95, each with its own n) and the per-request latency bar chart;
 * - the promotion gate for a test robot (the latest gate run of its revision);
 * - the choices for queuing an evaluation and the facts in an evaluation row;
 * - trace filter counts, span waterfalls and the trace drawer's fact groups.
 * Measured values only ever come from live data (`LiveInference`); everything
 * else is read from the workspace document with its own provenance.
 */
import { fmtCi, fmtCount, fmtMs, fmtNumber, fmtRatio, fmtSeconds, fmtShare, fmtTime, median, NOT_REPORTED, percentile } from "./format";
import type { LiveInference } from "./live";
import { getConfiguration, getSuite, latestGateRun, runsFor, spansForGroup, successShare, tracesFor } from "./selectors";
import type { RobotReadings, TraceFilter } from "./selectors";
import type {
  ActionTrace, ClockKind, ClockLabel, ConfigRevision, Configuration, ConvoyWorkspace, EvalRun, EvalSuite, FactGroup, ModelRole, ModelState, Percentiles,
  Robot, RobotHealth, RobotKind, RouteMode, SpanPath, TraceSpan,
} from "./types";

const finite = (value: number | null | undefined): value is number => typeof value === "number" && Number.isFinite(value);
const round = (value: number) => String(Math.round(value * 100) / 100);
const validTime = (at: string | null | undefined): at is string => !!at && Number.isFinite(Date.parse(at));

/* ---------- clocks and time windows ---------- */

/** "UTC", or a device clock as "PDT · UTC−7" / "IST · UTC+5:30". */
export function clockText(clock?: ClockLabel | null): string {
  if (!clock || (clock.zone === "UTC" && !clock.utcOffsetMinutes)) return "UTC";
  const offset = Math.abs(clock.utcOffsetMinutes), hours = Math.floor(offset / 60), minutes = offset % 60;
  return `${clock.zone} · UTC${clock.utcOffsetMinutes < 0 ? "−" : "+"}${hours}${minutes ? `:${String(minutes).padStart(2, "0")}` : ""}`;
}

function clockDay(ms: number, clock?: ClockLabel | null): string {
  return new Date(ms + (clock?.utcOffsetMinutes ?? 0) * 60000).toLocaleDateString("en-US", { timeZone: "UTC", month: "short", day: "numeric" });
}

/** The span of a set of times on a clock: "Oct 1 · 09:13 – 09:39 UTC", or with both days when it crosses midnight. Null when none is valid. */
export function timeWindowLabel(times: ReadonlyArray<string | null | undefined>, clock?: ClockLabel | null): string | null {
  const sorted = times.filter(validTime).map(at => Date.parse(at)).toSorted((a, b) => a - b);
  if (!sorted.length) return null;
  const first = sorted[0], last = sorted[sorted.length - 1];
  const zone = clock?.zone ?? "UTC";
  const time = (ms: number) => fmtTime(new Date(ms).toISOString(), clock ?? undefined, false);
  const fromDay = clockDay(first, clock), toDay = clockDay(last, clock);
  return fromDay === toDay ? `${fromDay} · ${time(first)} – ${time(last)} ${zone}` : `${fromDay}, ${time(first)} – ${toDay}, ${time(last)} ${zone}`;
}

/** "Oct 1, 07:58" (UTC, no zone: table headers name it); a recorded date with no time of day reads "Sep 29"; null reads "Not started". */
export function fmtStarted(at: string | null | undefined): string {
  if (!validTime(at)) return "Not started";
  const day = clockDay(Date.parse(at));
  return /T00:00(:00(\.0+)?)?(Z|[+-]00:?00)$/.test(at) ? day : `${day}, ${fmtTime(at, undefined, false)}`;
}

export const ROBOT_KIND_LABEL: Record<RobotKind, string> = { robot: "Physical robot", bench: "Hardware-in-the-loop bench", simulator: "Simulated robot" };

/**
 * Health to show with the flags in effect. Bound robots already derive health from
 * their flags; robots without a live binding carry a stored health, so a flag added
 * since (manual or prepared) raises it: attention to "Needs attention", warning to
 * "Degraded". Offline stands.
 */
export function displayHealth(readings: Pick<RobotReadings, "health" | "healthReason" | "flags">): { health: RobotHealth; reason: string | null } {
  if (readings.health === "offline") return { health: "offline", reason: readings.healthReason };
  const attention = readings.flags.find(flag => flag.severity === "attention");
  if (attention) return { health: "attention", reason: attention.label };
  const warning = readings.flags.find(flag => flag.severity === "warning");
  if (warning && (readings.health === "healthy" || readings.health === "not-reported")) return { health: "degraded", reason: warning.label };
  return { health: readings.health, reason: readings.healthReason };
}

/* ---------- on-device gateway spans (measured) ---------- */

/** One statistic of a received sample: median, nearest-rank p95 and the count of reported values. */
export interface SampleStat { p50: number | null; p95: number | null; n: number }
export function sampleStat(values: ReadonlyArray<number | null | undefined>): SampleStat {
  const reported = values.filter(finite);
  return { p50: median(reported), p95: percentile(reported, 0.95), n: reported.length };
}

export interface EdgeSpanStats {
  /** Spans received, with or without a latency. */
  received: number;
  /** Gateway (model) latency per request. */
  latency: SampleStat;
  /** Gateway-internal time to first token. */
  ttft: SampleStat;
  queue: SampleStat;
  /** Reported decode throughput, tokens/s. */
  throughput: SampleStat;
  outputTokens: SampleStat;
  /** Oldest and newest span start in the sample. */
  from: string | null;
  to: string | null;
}

/** Statistics of the received inference spans; every figure carries its own n (a span can miss one value). */
export function edgeSpanStats(inference: readonly LiveInference[]): EdgeSpanStats {
  const times = inference.map(item => item.at).filter(validTime).toSorted((a, b) => Date.parse(a) - Date.parse(b));
  return {
    received: inference.length,
    latency: sampleStat(inference.map(item => item.latencyMs)),
    ttft: sampleStat(inference.map(item => item.ttftMs)),
    queue: sampleStat(inference.map(item => item.queueMs)),
    throughput: sampleStat(inference.map(item => item.tokensPerS)),
    outputTokens: sampleStat(inference.map(item => item.tokensOut)),
    from: times[0] ?? null,
    to: times.at(-1) ?? null,
  };
}

const NICE_STEPS = [1, 2, 2.5, 5, 10];
/** Smallest round axis end at or above `high` that splits into at most `steps` round intervals (702 → 750 by 250; 120 → 150 by 50). */
export function niceAxis(high: number, steps = 3): { end: number; step: number } {
  if (!(high > 0) || !Number.isFinite(high)) return { end: steps, step: 1 };
  const raw = high / steps, power = 10 ** Math.floor(Math.log10(raw));
  const step = (NICE_STEPS.map(factor => factor * power).find(candidate => candidate >= raw - 1e-9) ?? 10 * power);
  const clean = (value: number) => Math.round(value * 1e6) / 1e6;
  return { end: clean(Math.ceil(high / step - 1e-9) * step), step: clean(step) };
}

export interface LatencyBarChart {
  /** One path for every bar, oldest on the left (viewBox 0 0 100 100, preserveAspectRatio none). */
  bars: string;
  grid: string;
  ticks: Array<{ value: number; label: string; top: string }>;
  /** Dashed p95 line and its label position from the top. */
  ref: string;
  refTop: string | null;
  end: number;
  count: number;
  low: number | null;
  high: number | null;
  p95: number | null;
}

/**
 * Bars of the reported latency per request (spans arrive newest first, bars run
 * oldest to newest); spans without a latency are left out. The axis keeps 5 %
 * headroom so the tallest bar and the p95 line never sit on the frame.
 */
export function latencyBarChart(inference: readonly LiveInference[]): LatencyBarChart {
  const values = inference.toReversed().map(item => item.latencyMs).filter(finite);
  const high = values.length ? Math.max(...values) : null, low = values.length ? Math.min(...values) : null;
  const { end, step } = niceAxis((high ?? 0) * 1.05);
  const y = (value: number) => 100 - Math.min(Math.max(value, 0), end) / end * 100;
  const width = values.length ? 100 / values.length : 0;
  const bars = values.map((value, i) => `M${round(i * width + width * 0.18)} 100V${round(y(value))}H${round((i + 1) * width - width * 0.18)}V100Z`).join("");
  const tickValues = Array.from({ length: Math.round(end / step) + 1 }, (_, i) => Math.round(i * step * 1e6) / 1e6);
  const p95 = percentile(values, 0.95);
  return {
    bars,
    grid: tickValues.map(value => `M0 ${round(y(value))}H100`).join(""),
    ticks: tickValues.map(value => ({ value, label: value.toLocaleString("en-US"), top: `${round(y(value))}%` })),
    ref: p95 === null ? "" : `M0 ${round(y(p95))}H100`,
    refTop: p95 === null ? null : `${round(y(p95))}%`,
    end, count: values.length, low, high, p95,
  };
}

/* ---------- promotion gate ---------- */

/** The first gate criterion a run missed, as "Each slice family: Network 48.3 % (gate ≥ 55 %)". */
export function gateShortfall(suite: EvalSuite | null, run: Pick<EvalRun, "gate">): string | null {
  const miss = run.gate?.results.find(result => !result.passed);
  if (!miss) return null;
  const criterion = suite?.gate.find(item => item.id === miss.criterionId);
  return criterion ? `${criterion.label}: ${miss.actual} (gate ${criterion.target})` : miss.actual;
}

export type PromotionState =
  | { kind: "allowed"; rev: string; run: EvalRun }
  | { kind: "blocked"; reason: string; run: EvalRun | null }
  | { kind: "not-test" };

/**
 * Whether a test robot may move to production: never for bench hardware, a
 * device-bound robot or a simulator; otherwise only when the latest gate run of
 * the revision it runs passed the gate.
 */
export function promotionState(ws: ConvoyWorkspace, robot: Robot, configuration: Configuration): PromotionState {
  if (robot.role !== "test") return { kind: "not-test" };
  if (robot.kind === "bench") return { kind: "blocked", reason: "Bench hardware runs evaluations; promote a revision, not a bench.", run: null };
  if (robot.kind === "simulator") return { kind: "blocked", reason: "A simulated robot runs suites only; it cannot serve production.", run: null };
  if (robot.deviceId) return { kind: "blocked", reason: "Bound to the connected test device; it cannot serve production.", run: null };
  if (!robot.rev || !configuration.revisions.some(revision => revision.rev === robot.rev)) return { kind: "blocked", reason: "Not running a revision of this configuration.", run: null };
  const run = latestGateRun(ws, configuration.id, robot.rev);
  if (!run || !run.gate) return { kind: "blocked", reason: `${robot.rev} has no gate decision yet; run the evaluation suite first.`, run: null };
  if (!run.gate.passed) {
    const shortfall = gateShortfall(getSuite(ws, run.suiteId), run);
    return { kind: "blocked", reason: `${robot.rev} is below the gate on Run ${run.number}${shortfall ? `: ${shortfall}` : ""}.`, run };
  }
  return { kind: "allowed", rev: robot.rev, run };
}

/* ---------- queuing an evaluation ---------- */

export const VARIANT_LABEL: Record<RouteMode, string> = { hybrid: "Hybrid", "cloud-only": "Cloud only", "edge-only": "Edge only" };

export interface EvaluationChoices {
  suites: Array<{ id: string; label: string; detail: string }>;
  suiteId: string | null;
  /** Testing revision first, then production, then the rest newest first. */
  revisions: Array<{ rev: string; label: string }>;
  rev: string | null;
}

/** Suites and revisions to offer, preselecting the configuration's suite and the revision the robot runs. */
export function evaluationChoices(ws: ConvoyWorkspace, configuration: Configuration, robot: Robot): EvaluationChoices {
  const suites = ws.suites.map(suite => ({ id: suite.id, label: `${suite.name} ${suite.version}`, detail: `${fmtCount(suite.episodesPerRun)} episodes per run · ${fmtCount(suite.seedsPerCell)} seeds per cell` }));
  const suiteId = suites.some(suite => suite.id === configuration.suiteId) ? configuration.suiteId : suites[0]?.id ?? null;
  const order = (rev: string) => rev === configuration.candidateRev ? 0 : rev === configuration.productionRev ? 1 : 2;
  const revisions = configuration.revisions.toReversed().toSorted((a, b) => order(a.rev) - order(b.rev)).map(revision => ({
    rev: revision.rev,
    label: `${revision.rev}${revision.rev === configuration.candidateRev ? " · testing" : revision.rev === configuration.productionRev ? " · in production" : ""}${revision.note ? ` · ${revision.note}` : ""}`,
  }));
  const rev = revisions.some(item => item.rev === robot.rev) ? robot.rev : revisions[0]?.rev ?? null;
  return { suites, suiteId, revisions, rev };
}

/** The gate criteria of a suite in one line: "Overall success ≥ 70 % · … · Critical safety events: None". */
export function gateSummary(suite: EvalSuite | null): string | null {
  return suite?.gate.length ? suite.gate.map(criterion => `${criterion.label}${/^[A-Za-z]/.test(criterion.target) ? ":" : ""} ${criterion.target}`).join(" · ") : null;
}

/** The newest run still running or queued on a robot. */
export function activeRunOn(ws: ConvoyWorkspace, robotId: string): EvalRun | null {
  return runsFor(ws, { robotId }).find(run => run.status === "running" || run.status === "queued") ?? null;
}

/* ---------- evaluation rows ---------- */

export interface RunCell { value: string; detail: string | null; missing: boolean }
export interface RunRowFacts {
  started: string;
  /** Configuration and revision under test. */
  subject: string;
  subjectDetail: string | null;
  success: RunCell;
  safety: RunCell;
  median: RunCell;
  /** Second line under the status: the gate shortfall, the reason it did not qualify, or the queued state. */
  note: string | null;
  /** Queued in the document with no runner connected (and not linked to a control-plane evaluation). */
  waiting: boolean;
  /** Control-plane evaluation id behind a recorded run. */
  evaluationId: string | null;
}

const CLOCK_TEXT: Record<ClockKind, string> = { simulated: "simulated", wall: "wall-clock", device: "device clock", browser: "browser clock", server: "server clock" };

/** What one evaluation row shows; missing values read "Pending" (queued) or "Not reported", never 0. */
export function runRowFacts(ws: ConvoyWorkspace, run: EvalRun): RunRowFacts {
  const queued = run.status === "queued", running = run.status === "running";
  const pending = (detail: string | null = null): RunCell => ({ value: queued ? "Pending" : NOT_REPORTED, detail, missing: true });
  const soFar = (text: string | null) => [text, running ? "so far" : null].filter(Boolean).join(" · ") || null;

  const { episodes, successes } = run.counts;
  let success: RunCell;
  if (successes === null || !episodes) success = pending(queued && run.progress ? `0 / ${fmtCount(run.progress.total)}` : null);
  else if (run.kind === "suite" || run.kind === "hil-latency" || episodes >= 20) {
    success = { value: fmtShare(successShare(run)), detail: soFar([`${fmtRatio(successes, episodes)}${run.kind === "hil-latency" ? " requests" : ""}`, !running && run.successCi95 ? `CI ${fmtCi(run.successCi95)}` : null].filter(Boolean).join(" · ")), missing: false };
  } else success = { value: fmtRatio(successes, episodes), detail: run.kind === "episode" ? (episodes === 1 ? "One episode" : `${fmtCount(episodes)} episodes`) : null, missing: false };

  const safety: RunCell = run.safety ? { value: fmtNumber(run.safety.per100, 1), detail: soFar(`${fmtCount(run.safety.critical)} critical`), missing: !finite(run.safety.per100) } : pending();

  let medianCell: RunCell;
  if (run.kind === "hil-latency" && run.hilLatency) medianCell = { value: fmtMs(run.hilLatency.plannerMs.p50), detail: `p95 ${fmtMs(run.hilLatency.plannerMs.p95)} · on-device gateway`, missing: !finite(run.hilLatency.plannerMs.p50) };
  else if (run.episodeTime && finite(run.episodeTime.medianS)) medianCell = { value: fmtSeconds(run.episodeTime.medianS), detail: soFar(CLOCK_TEXT[run.episodeTime.clock]), missing: false };
  else medianCell = pending();

  const waiting = queued && !run.recordedEvaluationId;
  const note = waiting ? "Runner not connected"
    : run.status === "below-gate" ? gateShortfall(getSuite(ws, run.suiteId), run)
    : run.status === "did-not-qualify" || run.status === "failed" ? run.failureModes[0]?.label ?? null
    : null;
  const configuration = getConfiguration(ws, run.configId);
  return {
    started: fmtStarted(run.startedAt),
    subject: `${configuration?.name ?? run.configId} ${run.rev}`,
    // A suite run's variant repeats its configuration; other runs name what actually ran.
    subjectDetail: (run.kind === "suite" ? run.purpose ?? run.variant : [run.variant, run.purpose].filter(Boolean).join(" · ")) || null,
    success, safety, median: medianCell, note, waiting,
    evaluationId: run.recordedEvaluationId ?? null,
  };
}

/* ---------- action traces ---------- */

export const TRACE_FILTER_LABEL: Record<TraceFilter, string> = { all: "All", fallback: "Fallback", escalated: "Escalated", failed: "Failed" };
export const TRACE_FILTERS: readonly TraceFilter[] = ["all", "fallback", "escalated", "failed"];
export const TRACE_EMPTY: Record<TraceFilter, string> = {
  all: "No actions are recorded for this robot.", fallback: "No fallback in this window.", escalated: "No escalations in this window.", failed: "No failed actions in this window.",
};

/** Count per filter chip, with the same matching as `tracesFor`. */
export function traceCounts(ws: ConvoyWorkspace, robotId: string): Record<TraceFilter, number> {
  return Object.fromEntries(TRACE_FILTERS.map(filter => [filter, tracesFor(ws, robotId, filter).length])) as Record<TraceFilter, number>;
}

/** "117 / 243 ms", one value when p50 and p95 agree, or "No policy call". */
export function policyLabel(policy: Percentiles | null | undefined): string {
  if (!policy) return "No policy call";
  if (!finite(policy.p50)) return NOT_REPORTED;
  return !finite(policy.p95) || policy.p95 === policy.p50 ? fmtMs(policy.p50) : `${fmtNumber(policy.p50, 0)} / ${fmtMs(policy.p95)}`;
}

export interface TraceWaterfall { id: string; title: string; unit: "ms" | "s"; spans: TraceSpan[]; paths: SpanPath[] }
const PATH_ORDER: readonly SpanPath[] = ["edge", "cloud", "fallback", "operator"];

/** One waterfall per span group (a trace without groups gets one for all spans), with the paths it uses for its legend. */
export function traceWaterfalls(trace: ActionTrace): TraceWaterfall[] {
  const groups = trace.spanGroups?.length ? trace.spanGroups : [{ id: "all", title: "Spans · whole trace", unit: "ms" as const }];
  return groups.map(group => {
    const spans = trace.spanGroups?.length ? spansForGroup(trace, group.id) : trace.spans;
    return { id: group.id, title: group.title, unit: group.unit, spans, paths: PATH_ORDER.filter(path => spans.some(span => span.path === path)) };
  }).filter(waterfall => waterfall.spans.length > 0);
}

export const MODEL_ROLE_LABEL: Record<ModelRole, string> = { planner: "Planner", policy: "Policy", "fallback-policy": "Fallback policy", "skill-pack": "Skill pack", verifier: "Verifier" };
export const MODEL_STATE_LABEL: Record<ModelState, string> = { active: "Active", "fallback-only": "Fallback only", proposed: "Proposed, not deployed", blocked: "Blocked", "not-deployed": "Not deployed" };
const MODEL_ROLE = MODEL_ROLE_LABEL;
const SERVING: ReadonlySet<ModelState> = new Set(["active", "fallback-only"]);
const PATH_TEXT: Record<ActionTrace["path"], string> = { edge: "Edge", cloud: "Cloud", fallback: "Fallback" };

/**
 * The drawer's fact groups in reading order: inputs and outputs, any other stored
 * groups (recovery, …), model versions, safety. Missing groups are built from the
 * trace's own fields; model versions fall back to what the revision declares and
 * say so; a trace without a safety record reads "Not reported".
 */
export function traceFactGroups(trace: ActionTrace, context: { revision?: ConfigRevision | null; configurationName?: string | null } = {}): FactGroup[] {
  const named = (title: string) => trace.facts.find(group => group.title.trim().toLowerCase() === title);
  const io: FactGroup = named("inputs and outputs") ?? {
    title: "Inputs and outputs",
    facts: [
      { label: "Instruction", value: trace.instruction, ...(trace.reference ? { detail: trace.reference } : {}) },
      trace.decision.kind === "skill"
        ? { label: "Planner decision", value: trace.decision.skill ?? NOT_REPORTED, detail: [trace.decision.params, finite(trace.plannerMs) ? `Planned in ${fmtMs(trace.plannerMs)}` : null].filter(Boolean).join(" · ") }
        : { label: "Planner decision", value: "Declined", detail: [trace.decision.reason, finite(trace.plannerMs) ? `Decided in ${fmtMs(trace.plannerMs)}` : null].filter(Boolean).join(" · ") },
      { label: "Route", value: trace.route, detail: `${PATH_TEXT[trace.path]} path` },
      { label: "Policy p50 / p95", value: policyLabel(trace.policyMs), ...(trace.policyMs?.n || trace.policyNote ? { detail: [trace.policyMs?.n ? `n = ${fmtCount(trace.policyMs.n)} calls` : null, trace.policyNote].filter(Boolean).join(" · ") } : {}) },
    ].map(fact => ("detail" in fact && !fact.detail ? { label: fact.label, value: fact.value } : fact)),
  };
  const revision = context.revision ?? null;
  const models: FactGroup | null = named("model versions") ?? (revision ? {
    title: "Model versions",
    facts: [
      ...revision.edgeModels.filter(model => SERVING.has(model.state)).map(model => ({ label: `${MODEL_ROLE[model.role]} · edge`, value: model.name, detail: model.runtime })),
      ...revision.cloudModels.filter(model => SERVING.has(model.state)).map(model => ({ label: `${MODEL_ROLE[model.role]} · cloud`, value: model.name, ...(model.serving ? { detail: model.serving } : {}) })),
      { label: "Configuration", value: `${context.configurationName ?? "Configuration"} ${trace.configRev ?? revision.rev}`, detail: "Models as declared by this revision; the trace does not record them" },
    ],
  } : null);
  const safety: FactGroup = named("safety") ?? { title: "Safety", facts: [{ label: "Safety record", value: NOT_REPORTED, detail: "No safety facts are stored with this trace." }] };
  const others = trace.facts.filter(group => ![io, models, safety].includes(group));
  return [io, ...others, ...(models ? [models] : []), safety];
}
