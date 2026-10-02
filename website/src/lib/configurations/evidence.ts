/**
 * Evidence panels: the latency budget of one planner decision, and autonomy (when an operator would
 * have to take over) across an eval's episodes. Pure functions over recorded values only:
 *
 * - an offline evaluation's per-episode `metrics` and `outcome`, as the import recorded them. The
 *   metric names are the simulator's (`integrations/simulation/src/convoy_sim/bimanual_pill_task/
 *   offline_replay.py`, `device_metrics`), whose planner figures come from real calls to the model;
 * - a live device's inference spans (on-device latency, first token and tokens per request).
 *
 * Nothing is modelled or filled in: a value an episode or span does not report is null and the pages
 * show "Not reported", never 0. Definitions (also in README.md):
 *
 * Latency budget, per planner decision (`p50` and `p95`):
 * - prefill ≈ time to first token on the device;
 * - decode ≈ on-device latency − time to first token;
 * - network / relay ≈ end-to-end round trip − on-device latency.
 * The formulas are applied to the percentile values, so the segments add up to the end-to-end figure.
 * An eval's figure is the median, across the episodes that report it, of each episode's own
 * percentile (each over that episode's calls). Live spans give the median and nearest-rank p95 of
 * the received requests; they carry no end-to-end time, so network / relay is not reported.
 *
 * Autonomy, an intervention is a recorded event where an operator would have to take over:
 * - a failed decision (`planner_failed_decisions`): no usable reply after the decision's retries;
 * - a protective stop (`protective_stops`): the simulator stops an arm when a contact force exceeds its
 *   safety threshold (the other arm, the bottle or the cap; a higher one for the table), so an
 *   arm–arm contact above the threshold is recorded here;
 * - an unfinished episode: outcome timeout, failure or safety stop (one per episode).
 * `arm_arm_contacts` counts every arm–arm contact at any force; it is shown but not counted, since it
 * has no threshold and the contacts above it already count as protective stops.
 * Decisions are resolved decisions: `planner_decisions` where the export reports it, else
 * `planner_valid_replies + planner_failed_decisions` (the same count: a valid reply ends a decision and a
 * failed decision ends without one; a decision still open when the episode ended is not counted).
 * "Accepted on the first call" is Σ `planner_first_call_accepted` ÷ Σ `planner_decisions`, the export's
 * decision-level counts (since the platform-chat-v1 transport); an eval whose episodes do not all report
 * both, as every earlier eval, shows Not reported. `planner_reasked_decisions` (accepted after a re-ask,
 * or failed) completes them: decisions = first-call accepted + re-asked.
 */
import { median, percentile } from "./format";
import type { LiveInference } from "./live";
import type { Configuration, LatencyTarget } from "./types";
import type { OfflineEpisode } from "../platform/client";

/** The per-episode metric names the panels read. */
export const EVIDENCE_METRICS = {
  e2eP50: "planner_e2e_p50_ms",
  e2eP95: "planner_e2e_p95_ms",
  deviceP50: "planner_device_p50_ms",
  deviceP95: "planner_device_p95_ms",
  ttftP50: "planner_ttft_p50_ms",
  /** Not in the simulator's export yet; read when present. */
  ttftP95: "planner_ttft_p95_ms",
  tokensIn: "planner_tokens_in_p50",
  tokensOut: "planner_tokens_out_p50",
  calls: "planner_calls",
  validReplies: "planner_valid_replies",
  failedDecisions: "planner_failed_decisions",
  /** Resolved decisions, as exported (valid replies + failed decisions); evals before it derive the same count. */
  decisions: "planner_decisions",
  /** Resolved decisions whose first call was accepted; its denominator is `decisions`. Absent before platform-chat-v1. */
  firstCallAccepted: "planner_first_call_accepted",
  /** Resolved decisions that needed a re-ask (accepted after it, or failed). */
  reaskedDecisions: "planner_reasked_decisions",
  protectiveStops: "protective_stops",
  armArmContacts: "arm_arm_contacts",
} as const;

type Metrics = OfflineEpisode["metrics"] | null | undefined;
type EpisodeRecord = Pick<OfflineEpisode, "outcome" | "metrics">;

const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
/** A reported duration or token figure: a finite number ≥ 0, else null. */
function amount(metrics: Metrics, name: string): number | null {
  const value = metrics && Object.hasOwn(metrics, name) ? metrics[name] : undefined;
  return finite(value) && value >= 0 ? value : null;
}
/** A reported count: a finite integer ≥ 0, else null. */
function count(metrics: Metrics, name: string): number | null {
  const value = amount(metrics, name);
  return value !== null && Number.isInteger(value) ? value : null;
}

/* ---------- latency budget ---------- */

/** One planner decision split by where its time goes. Null: not reported (or inconsistent, e.g. a negative difference). */
export interface LatencyBreakdown {
  /** ≈ prefill: time to first token on the device. */
  prefillMs: number | null;
  /** ≈ decode: on-device latency − time to first token. */
  decodeMs: number | null;
  /** On-device latency (prefill + decode). */
  deviceMs: number | null;
  /** ≈ network and relay: end-to-end round trip − on-device latency. */
  networkMs: number | null;
  /** End-to-end round trip: what the robot waited for the decision. */
  e2eMs: number | null;
}

export interface LatencyBudget {
  /** eval: an offline evaluation's per-episode metrics · live: a live device's received inference spans. */
  source: "eval" | "live";
  /** Episodes (eval) or requests (live) the figures come from. */
  n: number;
  p50: LatencyBreakdown;
  p95: LatencyBreakdown;
  /** Tokens per request at p50 (prompt in, reply out). */
  tokensIn: number | null;
  tokensOut: number | null;
  /** Prefill share of on-device time at p50, 0–1. */
  prefillShare: number | null;
  /** Eval only: the episode with the highest end-to-end p95, and its on-device p95 (relay stalls show here). */
  worst: { e2eMs: number; deviceMs: number | null } | null;
}

/** Applies the budget formulas to one percentile's first-token, on-device and end-to-end values. */
export function breakdown(ttftMs: number | null, deviceMs: number | null, e2eMs: number | null): LatencyBreakdown {
  return {
    prefillMs: ttftMs,
    decodeMs: deviceMs !== null && ttftMs !== null && deviceMs >= ttftMs ? deviceMs - ttftMs : null,
    deviceMs,
    networkMs: e2eMs !== null && deviceMs !== null && e2eMs >= deviceMs ? e2eMs - deviceMs : null,
    e2eMs,
  };
}

const LATENCY_NAMES = [EVIDENCE_METRICS.e2eP50, EVIDENCE_METRICS.e2eP95, EVIDENCE_METRICS.deviceP50, EVIDENCE_METRICS.deviceP95, EVIDENCE_METRICS.ttftP50, EVIDENCE_METRICS.ttftP95];

/**
 * An eval's latency budget from its episodes' recorded planner metrics: each figure is the median of
 * the episodes' own percentiles. Null when no episode reports a planner latency.
 */
export function latencyBudgetFromEpisodes(episodes: readonly EpisodeRecord[]): LatencyBudget | null {
  const reporting = episodes.filter(episode => LATENCY_NAMES.some(name => amount(episode.metrics, name) !== null));
  if (!reporting.length) return null;
  const across = (name: string) => median(reporting.map(episode => amount(episode.metrics, name)));
  const p50 = breakdown(across(EVIDENCE_METRICS.ttftP50), across(EVIDENCE_METRICS.deviceP50), across(EVIDENCE_METRICS.e2eP50));
  const p95 = breakdown(across(EVIDENCE_METRICS.ttftP95), across(EVIDENCE_METRICS.deviceP95), across(EVIDENCE_METRICS.e2eP95));
  let worst: LatencyBudget["worst"] = null;
  for (const episode of reporting) {
    const e2e = amount(episode.metrics, EVIDENCE_METRICS.e2eP95);
    if (e2e !== null && (!worst || e2e > worst.e2eMs)) worst = { e2eMs: e2e, deviceMs: amount(episode.metrics, EVIDENCE_METRICS.deviceP95) };
  }
  return {
    source: "eval", n: reporting.length, p50, p95,
    tokensIn: across(EVIDENCE_METRICS.tokensIn), tokensOut: across(EVIDENCE_METRICS.tokensOut),
    prefillShare: share(p50), worst,
  };
}

/**
 * A live device's latency budget from its received inference spans (median and nearest-rank p95, as the
 * Inference tile): prefill and decode only, since a span has no end-to-end time. Null without a latency.
 */
export function latencyBudgetFromInference(spans: readonly LiveInference[]): LatencyBudget | null {
  const measured = spans.filter(span => finite(span.latencyMs) && span.latencyMs >= 0);
  if (!measured.length) return null;
  const values = (pick: (span: LiveInference) => number | null) => measured.map(span => { const value = pick(span); return finite(value) && value >= 0 ? value : null; });
  const latency = values(span => span.latencyMs), ttft = values(span => span.ttftMs);
  const p50 = breakdown(median(ttft), median(latency), null);
  return {
    source: "live", n: measured.length, p50, p95: breakdown(percentile(ttft, 0.95), percentile(latency, 0.95), null),
    tokensIn: median(values(span => span.tokensIn)), tokensOut: median(values(span => span.tokensOut)),
    prefillShare: share(p50), worst: null,
  };
}

function share(p50: LatencyBreakdown): number | null {
  return p50.prefillMs !== null && p50.deviceMs !== null && p50.deviceMs > 0 && p50.prefillMs <= p50.deviceMs ? p50.prefillMs / p50.deviceMs : null;
}

/** A configuration's latency targets, shortest first (only well-formed ones; the validator checks the document). */
export function latencyTargets(config: Pick<Configuration, "latencyTargets"> | null | undefined): LatencyTarget[] {
  return (config?.latencyTargets ?? [])
    .filter(target => typeof target?.label === "string" && target.label.trim() !== "" && finite(target.ms) && target.ms > 0)
    .toSorted((a, b) => a.ms - b.ms);
}

/** A linear axis from 0: the domain end and evenly spaced ticks (steps of 1, 2, 2.5 or 5 × 10ⁿ ms, at most 4 intervals). */
export interface LatencyScale { maxMs: number; ticks: number[] }
export function latencyScale(values: ReadonlyArray<number | null | undefined>): LatencyScale {
  const high = Math.max(0, ...values.filter(finite));
  if (!(high > 0)) return { maxMs: 1, ticks: [0] };
  const raw = high / 4;
  const power = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map(m => m * power).find(candidate => candidate >= raw) ?? 10 * power;
  const intervals = Math.max(1, Math.ceil(high / step - 1e-9));
  return { maxMs: intervals * step, ticks: Array.from({ length: intervals + 1 }, (_, i) => Math.round(i * step * 1e6) / 1e6) };
}

/* ---------- autonomy ---------- */

export type InterventionType = "failed-decision" | "protective-stop" | "unfinished";
export type EvidenceRowType = InterventionType | "arm-arm-contact";

export interface InterventionRow {
  type: EvidenceRowType;
  /** Whether this type counts as an intervention (arm–arm contact is shown but not counted). */
  counted: boolean;
  /** Events across the episodes; null when an episode does not report it. */
  events: number | null;
  /** Episodes with at least one; null when an episode does not report it. */
  episodes: number | null;
}

export interface Autonomy {
  episodes: number;
  /** Episodes with no intervention; null when an episode's interventions are not fully reported. */
  autonomous: number | null;
  /** `autonomous / episodes`, 0–1. */
  autonomousShare: number | null;
  /** Interventions across the episodes (counted types). */
  interventions: number | null;
  /** Mean interventions per episode. */
  perEpisode: number | null;
  /** Resolved decisions: valid replies + failed decisions. */
  decisions: number | null;
  /** Interventions per 100 resolved decisions. */
  per100Decisions: number | null;
  /** Share of resolved decisions accepted on their first call, 0–1: Σ `planner_first_call_accepted` ÷ Σ `planner_decisions`. */
  firstCallAccepted: number | null;
  /** Mean resolved decisions between interventions (decisions / interventions); null when there were none. */
  decisionsBetween: number | null;
  /** Counted types first, then arm–arm contact when reported. */
  breakdown: InterventionRow[];
  /** Episodes that report every counted type (failed decisions, protective stops and a known outcome). */
  reported: number;
  /** Calls to the model and the valid replies among them, across the episodes. */
  calls: number | null;
  validReplies: number | null;
}

const OUTCOMES_UNFINISHED = new Set(["timeout", "failure", "safety-stop"]);
/** 1 for an unfinished episode, 0 for a success, null for an outcome this website does not know. */
function unfinished(outcome: unknown): number | null {
  return outcome === "success" ? 0 : typeof outcome === "string" && OUTCOMES_UNFINISHED.has(outcome) ? 1 : null;
}

/** Sum of per-episode values; null when any episode does not report it. */
const total = (values: ReadonlyArray<number | null>): number | null => values.some(value => value === null) ? null : values.reduce<number>((sum, value) => sum + (value ?? 0), 0);
/** Episodes with a value above 0; null when any episode does not report it. */
const affected = (values: ReadonlyArray<number | null>): number | null => values.some(value => value === null) ? null : values.filter(value => (value ?? 0) > 0).length;

/**
 * Autonomy across an eval's episodes from their recorded metrics and outcomes (see the module comment).
 * Null when there are no episodes, or no episode reports a failed-decision or protective-stop count
 * (an eval without planner or safety metrics).
 */
export function autonomyFromEpisodes(episodes: readonly EpisodeRecord[]): Autonomy | null {
  const relevant = episodes.some(episode => count(episode.metrics, EVIDENCE_METRICS.failedDecisions) !== null || count(episode.metrics, EVIDENCE_METRICS.protectiveStops) !== null);
  if (!episodes.length || !relevant) return null;
  const failed = episodes.map(episode => count(episode.metrics, EVIDENCE_METRICS.failedDecisions));
  const stops = episodes.map(episode => count(episode.metrics, EVIDENCE_METRICS.protectiveStops));
  const ends = episodes.map(episode => unfinished(episode.outcome));
  const contacts = episodes.map(episode => count(episode.metrics, EVIDENCE_METRICS.armArmContacts));

  // An episode is autonomous with every counted type reported as 0, not autonomous with any above 0, else unknown.
  const status = episodes.map((_, i) => {
    const values = [failed[i], stops[i], ends[i]];
    if (values.some(value => value !== null && value > 0)) return false;
    return values.every(value => value === 0) ? true : null;
  });
  const autonomous = status.some(value => value === null) ? null : status.filter(Boolean).length;
  const rows: InterventionRow[] = [
    { type: "failed-decision", counted: true, events: total(failed), episodes: affected(failed) },
    { type: "protective-stop", counted: true, events: total(stops), episodes: affected(stops) },
    { type: "unfinished", counted: true, events: total(ends), episodes: affected(ends) },
  ];
  if (contacts.some(value => value !== null)) rows.push({ type: "arm-arm-contact", counted: false, events: total(contacts), episodes: affected(contacts) });
  const interventions = total(rows.filter(row => row.counted).map(row => row.events));

  const valid = episodes.map(episode => count(episode.metrics, EVIDENCE_METRICS.validReplies));
  // The export's own decision count where it reports one; earlier evals give the same count as valid + failed.
  const exported = episodes.map(episode => count(episode.metrics, EVIDENCE_METRICS.decisions));
  const decisions = total(valid.map((value, i) => exported[i] ?? (value === null || failed[i] === null ? null : value + (failed[i] ?? 0))));
  // First-call acceptance over its own denominator: every episode must export both counts, consistently.
  const firstCalls = episodes.map(episode => count(episode.metrics, EVIDENCE_METRICS.firstCallAccepted));
  const firstCall = total(firstCalls), firstCallDecisions = total(exported);
  const consistent = firstCalls.every((value, i) => value === null || exported[i] === null || value <= exported[i]!);
  const n = episodes.length;
  return {
    episodes: n,
    autonomous, autonomousShare: autonomous === null ? null : autonomous / n,
    interventions, perEpisode: interventions === null ? null : interventions / n,
    decisions,
    per100Decisions: interventions !== null && decisions ? interventions / decisions * 100 : null,
    firstCallAccepted: firstCall !== null && firstCallDecisions && consistent ? firstCall / firstCallDecisions : null,
    decisionsBetween: interventions && decisions !== null ? decisions / interventions : null,
    breakdown: rows,
    reported: episodes.filter((_, i) => failed[i] !== null && stops[i] !== null && ends[i] !== null).length,
    calls: total(episodes.map(episode => count(episode.metrics, EVIDENCE_METRICS.calls))),
    validReplies: total(valid),
  };
}

/** Labels for the panels. */
export const INTERVENTION_LABEL: Record<EvidenceRowType, string> = {
  "failed-decision": "Failed decision",
  "protective-stop": "Protective stop",
  unfinished: "Unfinished episode",
  "arm-arm-contact": "Arm–arm contact",
};

/** One-line definitions for the panels' info toggles and tooltips (terse; the module comment has the detail). */
export const EVIDENCE_DEFINITIONS = {
  intervention: "A recorded event where an operator would take over: a failed decision, a protective stop or an unfinished episode.",
  autonomous: "Finished with no intervention.",
  decisions: "Resolved decisions: valid replies + failed decisions.",
  firstCall: "Resolved decisions whose first call was accepted (a valid reply).",
  between: "Resolved decisions ÷ interventions.",
  calls: "Calls to the model; a valid reply ends its decision.",
  reported: "Episodes reporting failed decisions, protective stops and a known outcome.",
  "failed-decision": "No usable reply after the decision's retries.",
  "protective-stop": "Arm stopped on contact above the safety threshold, arm–arm included.",
  unfinished: "Timeout, failure or safety stop: one per episode.",
  "arm-arm-contact": "Contacts at any force. Not counted: those above the threshold are protective stops.",
  prefill: "Time to first token on the device.",
  decode: "On-device latency − first token.",
  network: "End-to-end round trip − on-device latency.",
} as const;
