/**
 * Slices and planner calls of an offline evaluation, from its episodes' recorded metrics only. The
 * metric names are the simulator's (`integrations/simulation/src/convoy_sim/bimanual_pill_task/
 * offline_replay.py`, `device_metrics`).
 *
 * - A slice is the `slice` text an episode reports, e.g. "nominal". Episodes of different slices ran
 *   under different conditions, so the pages show their results side by side, never as one pooled
 *   rate. Slices keep the order in which they first appear among the episodes; episodes that report
 *   none form a "No slice" group. An eval with fewer than two groups is not sliced.
 * - Planner calls by result: a valid reply; a reply the executive refused (invalid choice, invalid
 *   JSON or invalid schema: the model answered, and the answer was not usable); a device or transport
 *   failure (device error, HTTP or transport error, timeout: no answer came back).
 *   Calls = valid + refused + failed.
 *
 * Nothing is filled in: a count an episode does not report makes every total it feeds null ("Not
 * reported"), never 0. A reported 0 stays 0.
 */
import { latencyBudgetFromEpisodes } from "./evidence";
import type { LatencyBudget } from "./evidence";
import { median } from "./format";
import type { OfflineEpisode } from "../platform/client";

/** The per-episode metric that names the episode's slice. */
export const SLICE_METRIC = "slice";
/** Planner calls by result, per episode. */
export const CALL_METRICS = {
  calls: "planner_calls",
  valid: "planner_valid_replies",
  invalidChoice: "planner_invalid_choice",
  invalidJson: "planner_invalid_json",
  invalidSchema: "planner_invalid_schema",
  deviceErrors: "planner_device_errors",
  httpErrors: "planner_http_errors",
  timeouts: "planner_timeouts",
} as const;
/** Replies the executive refused. */
export const REFUSED_METRICS = [CALL_METRICS.invalidChoice, CALL_METRICS.invalidJson, CALL_METRICS.invalidSchema] as const;
/** Calls that got no answer: device errors, HTTP or transport errors, timeouts. */
export const FAILED_METRICS = [CALL_METRICS.deviceErrors, CALL_METRICS.httpErrors, CALL_METRICS.timeouts] as const;
/** The task's items placed, and the items it had (the pill task reports pills). */
export const PLACED_METRICS = { placed: "pills_placed", total: "pills_total" } as const;
const FAILED_DECISIONS = "planner_failed_decisions";

type Metrics = OfflineEpisode["metrics"] | null | undefined;
/** What the selectors read of an episode: its outcome and metrics, and when present its seed, steps and times. */
export type SliceEpisode = Pick<OfflineEpisode, "outcome" | "metrics"> & Partial<Pick<OfflineEpisode, "seed" | "steps" | "wall_seconds" | "sim_seconds">>;

const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
/** A reported count: a finite integer ≥ 0, else null. */
function count(metrics: Metrics, name: string): number | null {
  const value = metrics && Object.hasOwn(metrics, name) ? metrics[name] : undefined;
  return finite(value) && value >= 0 && Number.isInteger(value) ? value : null;
}
/** Sum of per-episode values; null when any episode does not report one. */
const total = (values: ReadonlyArray<number | null>): number | null => values.some(value => value === null) ? null : values.reduce<number>((sum, value) => sum + (value ?? 0), 0);

/* ---------- slices ---------- */

export interface EvalSlice<E extends SliceEpisode = SliceEpisode> {
  /** The recorded slice text, trimmed; null for the episodes that report none. */
  id: string | null;
  /** `?slice=` value and key: the id, or a single space for the episodes that report none (no recorded slice can be blank). */
  key: string;
  /** "Nominal", "Pill count 30", "No slice". */
  label: string;
  /** The slice's episodes in upload order. */
  episodes: E[];
  successes: number;
  /** Distinct seeds, ascending. */
  seeds: number[];
}
export const NO_SLICE_KEY = " ";

/** A recorded slice as a label: separators become spaces and the first letter is capitalised ("pill_count_30" → "Pill count 30"). */
export function sliceLabel(id: string | null): string {
  const text = (id ?? "").replace(/[_\s]+/g, " ").trim();
  return text ? text[0].toUpperCase() + text.slice(1) : "No slice";
}

function sliceOf(metrics: Metrics): string | null {
  const value = metrics && Object.hasOwn(metrics, SLICE_METRIC) ? metrics[SLICE_METRIC] : null;
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

/** An eval's episodes by slice, in the order the slices first appear. One group (or none) means the eval is not sliced. */
export function evalSlices<E extends SliceEpisode>(episodes: readonly E[]): EvalSlice<E>[] {
  const groups = new Map<string | null, E[]>();
  for (const episode of episodes) {
    const id = sliceOf(episode.metrics);
    const list = groups.get(id);
    if (list) list.push(episode); else groups.set(id, [episode]);
  }
  return [...groups].map(([id, list]) => ({
    id, key: id ?? NO_SLICE_KEY, label: sliceLabel(id), episodes: list,
    successes: list.filter(episode => episode.outcome === "success").length,
    seeds: [...new Set(list.map(episode => episode.seed).filter((seed): seed is number => finite(seed)))].toSorted((a, b) => a - b),
  }));
}

/** Whether the episodes form two or more slices, so their results are shown per slice. */
export const isSliced = (slices: readonly EvalSlice[]): boolean => slices.length >= 2;

/** The slice `key` names, else the first. Null without slices. */
export function pickSlice<E extends SliceEpisode>(slices: readonly EvalSlice<E>[], key: string | null | undefined): EvalSlice<E> | null {
  return slices.find(slice => slice.key === key) ?? slices[0] ?? null;
}

/** Seeds as short text: "0–4" when consecutive, else listed ("0, 2, 7"). Null without seeds. */
export function seedText(seeds: readonly number[]): string | null {
  if (!seeds.length) return null;
  const first = seeds[0], last = seeds[seeds.length - 1];
  if (seeds.length === 1) return String(first);
  return seeds.every((seed, i) => seed === first + i) ? `${first}–${last}` : seeds.join(", ");
}

/** "Nominal 3/5 · Pill count 30 0/5": each slice's successes of its episodes. */
export function sliceResultsText(slices: readonly EvalSlice[]): string {
  return slices.map(slice => `${slice.label} ${slice.successes}/${slice.episodes.length}`).join(" · ");
}

/* ---------- planner calls ---------- */

export interface CallGroup {
  /** Calls with this result across the episodes; null when an episode does not report one of its parts. */
  calls: number | null;
  /** Episodes with at least one such call; null when an episode does not report one of its parts. */
  episodes: number | null;
  /** Each part's calls across the episodes, by metric name (null: an episode does not report it). */
  parts: Array<{ name: string; calls: number | null }>;
}
export interface PlannerCalls {
  /** Calls to the model across the episodes. */
  calls: number | null;
  valid: CallGroup;
  /** Replies the executive refused: invalid choice, invalid JSON or invalid schema. */
  refused: CallGroup;
  /** No answer: device errors, HTTP or transport errors, timeouts. */
  failed: CallGroup;
}

function group(episodes: readonly SliceEpisode[], names: readonly string[]): CallGroup {
  const perEpisode = episodes.map(episode => total(names.map(name => count(episode.metrics, name))));
  return {
    calls: total(perEpisode),
    episodes: perEpisode.some(value => value === null) ? null : perEpisode.filter(value => (value ?? 0) > 0).length,
    parts: names.map(name => ({ name, calls: total(episodes.map(episode => count(episode.metrics, name))) })),
  };
}

/**
 * The episodes' planner calls by result. Null when no episode reports a refused or failed count (an
 * eval without the device planner's call results), so nothing is shown rather than zeros.
 */
export function plannerCalls(episodes: readonly SliceEpisode[]): PlannerCalls | null {
  const reported = episodes.some(episode => [...REFUSED_METRICS, ...FAILED_METRICS].some(name => count(episode.metrics, name) !== null));
  if (!episodes.length || !reported) return null;
  return {
    calls: total(episodes.map(episode => count(episode.metrics, CALL_METRICS.calls))),
    valid: group(episodes, [CALL_METRICS.valid]),
    refused: group(episodes, REFUSED_METRICS),
    failed: group(episodes, FAILED_METRICS),
  };
}

/* ---------- one slice's results ---------- */

export interface SliceSummary {
  slice: EvalSlice;
  /** Items placed of the items the episodes had; null unless every episode reports both. */
  placed: { placed: number; total: number } | null;
  /** Decisions with no usable reply after their retries. */
  failedDecisions: number | null;
  calls: PlannerCalls | null;
  /** On-device and end-to-end latency per planner decision (medians of the episodes' percentiles, as the latency budget). */
  latency: LatencyBudget | null;
  medianSteps: number | null;
  /** Median episode time: wall clock when the episodes report it, else simulated. */
  medianSeconds: number | null;
  clock: "wall" | "simulated" | null;
}

export function sliceSummary(slice: EvalSlice): SliceSummary {
  const { episodes } = slice;
  const placed = total(episodes.map(episode => count(episode.metrics, PLACED_METRICS.placed)));
  const items = total(episodes.map(episode => count(episode.metrics, PLACED_METRICS.total)));
  const wall = median(episodes.map(episode => episode.wall_seconds));
  const simulated = median(episodes.map(episode => episode.sim_seconds));
  return {
    slice,
    placed: placed !== null && items !== null && episodes.length ? { placed, total: items } : null,
    failedDecisions: episodes.length ? total(episodes.map(episode => count(episode.metrics, FAILED_DECISIONS))) : null,
    calls: plannerCalls(episodes),
    latency: latencyBudgetFromEpisodes(episodes),
    medianSteps: median(episodes.map(episode => episode.steps)),
    medianSeconds: wall ?? simulated,
    clock: wall !== null ? "wall" : simulated !== null ? "simulated" : null,
  };
}
