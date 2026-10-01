/**
 * Pure helpers for the evaluation-run page and its rollout replay: confidence
 * intervals, gate rows, slice-family aggregates, safety and failure rows, the
 * rollouts table (sort, page) and the replay timeline (markers, path segments,
 * key steps). No React; the page lives in src/components/configurations/pages.
 */
import { wilson } from "./format";
import { getConfiguration, getRun, latestGateRun, runsFor, successShare } from "./selectors";
import type {
  ClockKind, ConvoyWorkspace, EvalRun, EvalSuite, PathKind, RolloutEvent, Rollout, RolloutStep, RunSafety, Severity, SliceResult, UniformSeries,
} from "./types";

const finite = (value: number | null | undefined): value is number => typeof value === "number" && Number.isFinite(value);
const round1 = (value: number) => Math.round(value * 10) / 10;

/* ---------- statistics ---------- */

/**
 * The 95 % interval of a success share: the stored interval when the run has one,
 * otherwise the Wilson score interval from the counts (`computed`), or null when
 * the run is not scored or has no episodes.
 */
export function successInterval(run: Pick<EvalRun, "counts" | "successCi95">): { ci: [number, number] | null; computed: boolean } {
  if (run.successCi95) return { ci: run.successCi95, computed: false };
  const { successes, episodes } = run.counts;
  if (successes === null || !episodes) return { ci: null, computed: false };
  return { ci: wilson(successes, episodes), computed: true };
}
/** A result row's interval (slice or task): stored `ci95`, else Wilson from its counts. */
export function rowInterval(row: { successes: number; episodes: number; ci95?: readonly [number, number] | null }): [number, number] | null {
  if (row.ci95) return [row.ci95[0], row.ci95[1]];
  return row.episodes > 0 ? wilson(row.successes, row.episodes) : null;
}
/** Success share of a row, or null without episodes. */
export const rowShare = (row: { successes: number; episodes: number }): number | null => row.episodes > 0 ? row.successes / row.episodes : null;
/** Difference of two shares in percentage points, to 0.1 (78.1 % vs 75.3 % → 2.8); null when either is missing. */
export function pointsDelta(share: number | null | undefined, baseline: number | null | undefined): number | null {
  return finite(share) && finite(baseline) ? round1((share - baseline) * 100) : null;
}
/** Plain difference to 0.1 (safety per 100, seconds); null when either is missing. */
export function valueDelta(value: number | null | undefined, baseline: number | null | undefined): number | null {
  return finite(value) && finite(baseline) ? round1(value - baseline) : null;
}
/** "+2.8", "−0.9", "0.0" (typographic minus). */
export function signed(value: number, digits = 1): string {
  const text = Math.abs(value).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return value > 0 ? `+${text}` : value < 0 ? `−${text}` : text;
}

/* ---------- time ---------- */

/** 41.2 → "41.2 s", 197 → "3 min 17 s", 23400 → "6 h 30 min"; null → null. */
export function fmtDuration(seconds: number | null | undefined): string | null {
  if (!finite(seconds) || seconds < 0) return null;
  if (seconds < 60) return `${seconds.toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} s`;
  const total = Math.round(seconds);
  if (total < 3600) { const s = total % 60; return `${Math.floor(total / 60)} min${s ? ` ${s} s` : ""}`; }
  const minutes = Math.round(total / 60), m = minutes % 60;
  return `${Math.floor(minutes / 60)} h${m ? ` ${m} min` : ""}`;
}
/** Seconds between two ISO times, or null. */
export function secondsBetween(from: string | null | undefined, to: string | number | null | undefined): number | null {
  const a = from ? Date.parse(from) : Number.NaN;
  const b = typeof to === "number" ? to : to ? Date.parse(to) : Number.NaN;
  return Number.isFinite(a) && Number.isFinite(b) && b >= a ? (b - a) / 1000 : null;
}
const CLOCK_LABEL: Record<ClockKind, string> = { simulated: "simulated", wall: "wall-clock", device: "device clock", browser: "browser clock", server: "server clock" };
const CLOCK_NAME: Record<ClockKind, string> = { simulated: "simulated clock", wall: "wall clock", device: "device clock", browser: "browser clock", server: "server clock" };
/** Unit suffix naming the clock a time was read from: "15.6 s simulated", "46.3 s wall-clock". */
export const clockLabel = (clock: ClockKind) => CLOCK_LABEL[clock];
/** The clock as a noun: "on the simulated clock", "on the wall clock". */
export const clockName = (clock: ClockKind) => CLOCK_NAME[clock];
/** What a run counts: requests for a latency soak, episodes otherwise. */
export const countNoun = (run: Pick<EvalRun, "kind">): { one: string; many: string } => run.kind === "hil-latency" ? { one: "request", many: "requests" } : { one: "episode", many: "episodes" };

/* ---------- gate ---------- */

const PERCENT = /(\d+(?:\.\d+)?)\s*%/;
/**
 * The share threshold a suite's gate states for overall success or for slice
 * families ("≥ 70 %" → 0.7), used to draw the dashed gate marker. Null when the
 * suite states no percentage for it: the marker is then not drawn.
 */
export function gateThreshold(suite: EvalSuite | null | undefined, kind: "overall" | "slice"): number | null {
  for (const criterion of suite?.gate ?? []) {
    const text = `${criterion.id} ${criterion.label}`;
    const matches = kind === "slice" ? /famil|(each|every|per|any)[\s-]+slice/i.test(text)
      : criterion.id === "overall" || /overall/i.test(criterion.label) || (/success/i.test(criterion.label) && !/famil|each|every|\bper\b|task|nominal/i.test(criterion.label));
    const found = matches ? PERCENT.exec(criterion.target) : null;
    if (found) return Number(found[1]) / 100;
  }
  return null;
}

export type GateState = "pass" | "fail" | "pending";
export interface GateRow { id: string; label: string; target: string | null; state: GateState; actual: string | null }
/** One row per suite criterion with its result; criteria without a result are pending. */
export function gateRows(suite: EvalSuite | null | undefined, run: Pick<EvalRun, "gate">): GateRow[] {
  const results = new Map((run.gate?.results ?? []).map(result => [result.criterionId, result]));
  const rows: GateRow[] = (suite?.gate ?? []).map(criterion => {
    const result = results.get(criterion.id);
    return { id: criterion.id, label: criterion.label, target: criterion.target, state: result ? (result.passed ? "pass" : "fail") : "pending", actual: result?.actual ?? null };
  });
  for (const result of run.gate?.results ?? []) {
    if (!rows.some(row => row.id === result.criterionId)) rows.push({ id: result.criterionId, label: result.criterionId, target: null, state: result.passed ? "pass" : "fail", actual: result.actual });
  }
  return rows;
}

/* ---------- slices ---------- */

export interface FamilyResult { id: string; name: string; episodes: number; successes: number; share: number | null; ci: [number, number] | null; safetyPer100: number | null; slices: SliceResult[] }
/**
 * Slice results summed per family (a family's slices partition its episodes),
 * in suite order; families without results are left out. Safety per 100 is
 * episode-weighted over the slices that report it.
 */
export function familyResults(suite: EvalSuite | null | undefined, results: readonly SliceResult[]): FamilyResult[] {
  const byId = new Map(results.map(result => [result.sliceId, result]));
  return (suite?.sliceFamilies ?? []).flatMap(family => {
    const slices = family.slices.map(slice => byId.get(slice.id)).filter((result): result is SliceResult => !!result);
    if (!slices.length) return [];
    const episodes = slices.reduce((sum, slice) => sum + slice.episodes, 0);
    const successes = slices.reduce((sum, slice) => sum + slice.successes, 0);
    const safe = slices.filter(slice => finite(slice.safetyPer100) && slice.episodes > 0);
    const safeEpisodes = safe.reduce((sum, slice) => sum + slice.episodes, 0);
    const safetyPer100 = safeEpisodes ? round1(safe.reduce((sum, slice) => sum + (slice.safetyPer100 as number) * slice.episodes, 0) / safeEpisodes) : null;
    return [{ id: family.id, name: family.name, episodes, successes, share: episodes ? successes / episodes : null, ci: episodes ? wilson(successes, episodes) : null, safetyPer100, slices }];
  });
}
/** The family a slice belongs to. */
export function familyOf(suite: EvalSuite | null | undefined, sliceId: string): { id: string; name: string } | null {
  const family = suite?.sliceFamilies.find(item => item.slices.some(slice => slice.id === sliceId));
  return family ? { id: family.id, name: family.name } : null;
}

export interface SliceGap { sliceId: string; mine: SliceResult; theirs: SliceResult; points: number }
/** The slice with the largest success difference between two runs of one suite (ties: the first in `mine` order). */
export function largestSliceGap(mine: readonly SliceResult[], theirs: readonly SliceResult[]): SliceGap | null {
  const other = new Map(theirs.map(result => [result.sliceId, result]));
  let best: SliceGap | null = null;
  for (const result of mine) {
    const match = other.get(result.sliceId);
    const points = match ? pointsDelta(rowShare(result), rowShare(match)) : null;
    if (match && points !== null && (!best || Math.abs(points) > Math.abs(best.points))) best = { sliceId: result.sliceId, mine: result, theirs: match, points };
  }
  return best;
}

/* ---------- safety and failures ---------- */

export interface SafetyRow { id: string; name: string; severity: Severity | null; count: number }
/** Violation counts per check in the suite's order (zero-filled), then checks the suite does not define. */
export function safetyRows(suite: EvalSuite | null | undefined, safety: RunSafety | null | undefined): SafetyRow[] {
  const counts = new Map((safety?.byCheck ?? []).map(entry => [entry.checkId, entry.count]));
  const rows: SafetyRow[] = (suite?.safety ?? []).map(check => ({ id: check.id, name: check.name, severity: check.severity, count: counts.get(check.id) ?? 0 }));
  for (const entry of safety?.byCheck ?? []) if (!rows.some(row => row.id === entry.checkId)) rows.push({ id: entry.checkId, name: entry.checkId, severity: null, count: entry.count });
  return rows;
}
/** Episodes that did not succeed so far, or null when the run is not scored. */
export function failedEpisodes(run: Pick<EvalRun, "counts">): number | null {
  return run.counts.successes === null ? null : run.counts.episodes - run.counts.successes;
}
/** Each failure mode's share of the failed episodes, in percent (rounded). */
export function failureShares(items: ReadonlyArray<{ label: string; count: number }>, failed: number | null): Array<{ label: string; count: number; pct: number | null }> {
  return items.toSorted((a, b) => b.count - a.count).map(item => ({ ...item, pct: failed ? Math.round(item.count / failed * 100) : null }));
}

/* ---------- comparison runs ---------- */

/**
 * The run this one is compared with: its baseline, else the newest gated run of
 * the same suite on the configuration's production revision. Null for the
 * production revision itself, for runs without a suite and when none exists.
 */
export function comparisonRun(ws: ConvoyWorkspace, run: EvalRun): EvalRun | null {
  if (run.kind !== "suite" || !run.suiteId) return null;
  const baseline = run.baselineRunId ? getRun(ws, run.baselineRunId) : null;
  if (baseline && baseline.id !== run.id) return baseline;
  const productionRev = getConfiguration(ws, run.configId)?.productionRev ?? null;
  if (!productionRev || productionRev === run.rev) return null;
  const candidate = latestGateRun(ws, run.configId, productionRev);
  return candidate && candidate.id !== run.id && candidate.suiteId === run.suiteId ? candidate : null;
}
/** The newest finished, gated run of the same suite on another configuration, with slice results. */
export function otherConfigurationRun(ws: ConvoyWorkspace, run: EvalRun): EvalRun | null {
  if (run.kind !== "suite" || !run.suiteId) return null;
  return runsFor(ws).find(other => other.id !== run.id && other.configId !== run.configId && other.kind === "suite" && other.suiteId === run.suiteId
    && other.gate !== null && other.slices.length > 0) ?? null;
}
/** The newest latency soak on bench hardware for a robot, for the edge latency tile. */
export function hilRunFor(ws: ConvoyWorkspace, robotId: string): EvalRun | null {
  return runsFor(ws, { robotId }).find(run => run.kind === "hil-latency" && !!run.hilLatency) ?? null;
}
/** Success share of a run as a number for deltas. */
export const runShare = (run: Pick<EvalRun, "counts"> | null | undefined) => run ? successShare(run) : null;

/* ---------- rollouts table ---------- */

export type RolloutSort = "events" | "seed" | "duration" | "episode";
export const ROLLOUT_SORTS: ReadonlyArray<{ value: RolloutSort; label: string }> = [
  { value: "events", label: "Most events" }, { value: "seed", label: "Seed" }, { value: "duration", label: "Longest first" }, { value: "episode", label: "Episode" },
];
const collator = new Intl.Collator("en-US", { numeric: true, sensitivity: "base" });
/** Rollouts sorted for the table: most events (events and violations), seed, longest first, or episode id. */
export function sortRollouts(rollouts: readonly Rollout[], sort: RolloutSort): Rollout[] {
  const events = (rollout: Rollout) => rollout.events.length + rollout.violations.length;
  return rollouts.toSorted((a, b) => (sort === "events" ? events(b) - events(a) : sort === "seed" ? a.seed - b.seed : sort === "duration" ? b.durationS - a.durationS : 0) || collator.compare(a.id, b.id));
}
/** Counts for the outcome chips. */
export function outcomeCounts(rollouts: readonly Rollout[]): { all: number; failed: number; safety: number } {
  return {
    all: rollouts.length,
    failed: rollouts.filter(rollout => rollout.outcome !== "succeeded").length,
    safety: rollouts.filter(rollout => rollout.violations.length > 0 || rollout.outcome === "safety-stop").length,
  };
}
export interface Page<T> { items: T[]; page: number; pages: number; from: number; to: number; total: number }
/** One page (0-based, clamped) of `size` items, with 1-based `from`–`to` for "Showing 11–12 of 12". */
export function paginate<T>(items: readonly T[], page: number, size: number): Page<T> {
  const total = items.length, pages = Math.max(1, Math.ceil(total / size));
  const current = Math.min(Math.max(0, Math.floor(page) || 0), pages - 1);
  const start = current * size;
  const slice = items.slice(start, start + size);
  return { items: slice, page: current, pages, from: slice.length ? start + 1 : 0, to: start + slice.length, total };
}
/** A one-line note for a rollout's outcome cell: its failure mode, else its first warning event. */
export function rolloutNote(rollout: Rollout): string | null {
  return rollout.failureMode ?? rollout.events.find(event => event.tone === "warning")?.label ?? null;
}

/* ---------- replay timeline ---------- */

/** Steps in a rollout (at least 1) and its step ↔ simulated-second conversion. */
export function replayClock(rollout: Pick<Rollout, "steps" | "durationS" | "rateHz">): { steps: number; rate: number; atStep: (step: number) => number; stepAt: (seconds: number) => number } {
  const steps = Math.max(1, rollout.steps);
  const rate = finite(rollout.rateHz) && rollout.rateHz > 0 ? rollout.rateHz : steps / Math.max(rollout.durationS, 1e-9);
  return {
    steps, rate,
    atStep: step => Math.min(rollout.durationS, Math.max(0, step) / rate),
    stepAt: seconds => Math.min(steps, Math.max(0, Math.round(seconds * rate))),
  };
}
/** Index of the last item at or before `atS` (−1 when none). Items are sorted by time. */
export function lastAtOrBefore<T extends { atS: number }>(items: readonly T[], atS: number): number {
  let index = -1;
  items.forEach((item, i) => { if (item.atS <= atS + 1e-9) index = i; });
  return index;
}

export interface ScrubMarker { atS: number; share: number; label: string; tone: RolloutEvent["tone"]; events: RolloutEvent[]; edge: "start" | "end" | null; labelled: boolean }
/**
 * Scrubber markers for a rollout's events. Events closer than `minGap` (share of
 * the duration) to the previous marker merge into it, so targets never overlap;
 * a merged marker takes the most severe tone and reads "Label +1". Markers in the
 * first or last 10 % align to that edge. A marker shows its label only when the
 * label (about `labelWidth` of the rail) clears the previous visible label; every
 * marker keeps its tick and accessible name.
 */
export function scrubMarkers(events: readonly RolloutEvent[], durationS: number, minGap = 0.08, labelWidth = 0.15): ScrubMarker[] {
  const total = durationS > 0 ? durationS : 1;
  const markers: ScrubMarker[] = [];
  const rank = { warning: 2, good: 1, info: 0 } as const;
  for (const event of events.toSorted((a, b) => a.atS - b.atS)) {
    const share = Math.min(Math.max(event.atS / total, 0), 1);
    const last = markers.at(-1);
    if (last && share - last.share < minGap) {
      last.events.push(event);
      if (rank[event.tone] > rank[last.tone]) last.tone = event.tone;
      last.label = `${last.events[0].label} +${last.events.length - 1}`;
      continue;
    }
    markers.push({ atS: event.atS, share, label: event.label, tone: event.tone, events: [event], edge: null, labelled: true });
  }
  let visibleUntil = -Infinity;
  for (const marker of markers) {
    marker.edge = marker.share >= 0.9 ? "end" : marker.share <= 0.1 ? "start" : null;
    const from = marker.edge === "end" ? marker.share - labelWidth : marker.edge === "start" ? marker.share : marker.share - labelWidth / 2;
    marker.labelled = from >= visibleUntil + 0.01;
    if (marker.labelled) visibleUntil = from + labelWidth;
  }
  return markers;
}

export interface PathSegment { path: PathKind; from: number; to: number }
/**
 * Contiguous policy-path segments from a rollout's key steps: a step without a
 * planner decision starts or continues its path until the next path change, a
 * step without a path, or the end. Planner decisions are not policy time.
 */
export function pathSegments(steps: readonly RolloutStep[] | undefined, durationS: number): PathSegment[] {
  const segments: PathSegment[] = [];
  let open: PathSegment | null = null;
  for (const step of (steps ?? []).toSorted((a, b) => a.atS - b.atS)) {
    if (step.planner !== null && step.path === "edge") continue;
    const path = step.path ?? null;
    if (open && path === open.path) continue;
    if (open) { open.to = step.atS; if (open.to > open.from) segments.push(open); open = null; }
    if (path) open = { path, from: step.atS, to: durationS };
  }
  if (open && durationS > open.from) segments.push({ ...open, to: durationS });
  return segments;
}
/** Seconds per path, to 0.1. */
export function pathTotals(segments: readonly PathSegment[]): Array<{ path: PathKind; seconds: number }> {
  const totals = new Map<PathKind, number>();
  for (const segment of segments) totals.set(segment.path, (totals.get(segment.path) ?? 0) + segment.to - segment.from);
  return [...totals].map(([path, seconds]) => ({ path, seconds: round1(seconds) }));
}
/** The policy path at `atS`, or null outside every segment. */
export function pathAt(segments: readonly PathSegment[], atS: number): PathKind | null {
  return segments.find(segment => atS >= segment.from - 1e-9 && atS < segment.to - 1e-9)?.path ?? null;
}
/** Steps worth stopping at (events and key steps), for reduced-motion playback. */
export function keySteps(rollout: Pick<Rollout, "events" | "stepDetail" | "steps" | "durationS" | "rateHz">): number[] {
  const clock = replayClock(rollout);
  const steps = new Set<number>([0, clock.steps]);
  for (const event of rollout.events) steps.add(clock.stepAt(event.atS));
  for (const step of rollout.stepDetail ?? []) steps.add(Math.min(clock.steps, Math.max(0, step.step)));
  return [...steps].toSorted((a, b) => a - b);
}
/** `size` rows around the current one (as the mock's "steps around the playhead"). */
export function windowAround<T>(rows: readonly T[], current: number, size = 5): { start: number; rows: T[] } {
  const start = Math.max(0, Math.min(current - 2, rows.length - size));
  return { start, rows: rows.slice(start, start + size) };
}
/** A uniform series cut to the rollout duration: values with their times, for a synced plot. */
export function seriesWithin(series: UniformSeries | undefined, durationS: number): { values: Array<number | null>; spanS: number } | null {
  if (!series || !(series.stepS > 0)) return null;
  const values = series.values.filter((_, i) => i * series.stepS <= durationS + 1e-9);
  return values.length > 1 ? { values, spanS: (values.length - 1) * series.stepS } : null;
}
