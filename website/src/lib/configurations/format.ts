/**
 * Formatting for the Configurations pages. House style: mono numerics with a
 * space before the unit ("27 %", "181 ms"), en-US grouping, "Not reported" for
 * a missing value (a reported zero stays zero), UTC unless a device clock is
 * given, and dated evidence labels ("Recorded · Sep 14").
 */
import type { ClockLabel, Provenance, SampledSeries, SeriesPoint } from "./types";

export const NOT_REPORTED = "Not reported";

const finite = (value: number | null | undefined): value is number => typeof value === "number" && Number.isFinite(value);

/** 1234.5 → "1,234.5"; null → "Not reported". `digits` is the maximum number of fraction digits. */
export function fmtNumber(value: number | null | undefined, digits = 1): string {
  return finite(value) ? value.toLocaleString("en-US", { maximumFractionDigits: digits }) : NOT_REPORTED;
}
/** Fixed fraction digits: 46 → "46.0". */
export function fmtFixed(value: number | null | undefined, digits = 1): string {
  return finite(value) ? value.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits }) : NOT_REPORTED;
}
/** 46 → "46.0 °C" with `fmtFixed`, or "Not reported". */
export function fmtUnit(value: number | null | undefined, unit: string, digits = 1, fixed = false): string {
  if (!finite(value)) return NOT_REPORTED;
  return `${fixed ? fmtFixed(value, digits) : fmtNumber(value, digits)} ${unit}`;
}
export const fmtMs = (value: number | null | undefined, digits = 0) => fmtUnit(value, "ms", digits);
export const fmtSeconds = (value: number | null | undefined, digits = 1) => fmtUnit(value, "s", digits, true);
/** A percentage value (0–100): 78.06 → "78.1 %". */
export const fmtPct = (value: number | null | undefined, digits = 1) => fmtUnit(value, "%", digits, digits > 0);
/** A share (0–1): 0.7806 → "78.1 %". */
export const fmtShare = (share: number | null | undefined, digits = 1) => finite(share) ? fmtPct(share * 100, digits) : NOT_REPORTED;
export const fmtCount = (value: number | null | undefined) => fmtNumber(value, 0);
/** "281 / 360". */
export function fmtRatio(part: number | null | undefined, whole: number | null | undefined): string {
  return finite(part) && finite(whole) ? `${fmtCount(part)} / ${fmtCount(whole)}` : NOT_REPORTED;
}
/** 95 % interval of a share: [0.735, 0.82] → "73.5–82.0 %". */
export function fmtCi(ci: readonly [number, number] | null | undefined, digits = 1): string {
  return ci && finite(ci[0]) && finite(ci[1]) ? `${fmtFixed(ci[0] * 100, digits)}–${fmtFixed(ci[1] * 100, digits)} %` : NOT_REPORTED;
}

/* ---------- Time ---------- */

const parse = (at: string | null | undefined) => (at ? Date.parse(at) : Number.NaN);
const shifted = (ms: number, clock?: ClockLabel) => new Date(ms + (clock?.utcOffsetMinutes ?? 0) * 60000);
const zone = (clock?: ClockLabel) => clock?.zone ?? "UTC";

/** "09:41:20" in UTC or the given device clock. */
export function fmtTime(at: string | null | undefined, clock?: ClockLabel, seconds = true): string {
  const ms = parse(at);
  if (!Number.isFinite(ms)) return NOT_REPORTED;
  return shifted(ms, clock).toLocaleTimeString("en-US", { timeZone: "UTC", hour: "2-digit", minute: "2-digit", ...(seconds ? { second: "2-digit" } : {}), hourCycle: "h23" });
}
/** "Oct 1, 09:41:20 UTC" (or the device clock's zone). */
export function fmtDateTime(at: string | null | undefined, clock?: ClockLabel, seconds = true): string {
  const ms = parse(at);
  if (!Number.isFinite(ms)) return NOT_REPORTED;
  const day = shifted(ms, clock).toLocaleDateString("en-US", { timeZone: "UTC", month: "short", day: "numeric" });
  return `${day}, ${fmtTime(at, clock, seconds)} ${zone(clock)}`;
}
/** "Sep 14". */
export function fmtDate(at: string | null | undefined): string {
  const ms = parse(at);
  return Number.isFinite(ms) ? new Date(ms).toLocaleDateString("en-US", { timeZone: "UTC", month: "short", day: "numeric" }) : NOT_REPORTED;
}
/** "Sep 13–14", "Sep 30 – Oct 1", or one date when both fall on the same day. */
export function fmtDateRange(at: string | null | undefined, until: string | null | undefined): string {
  const from = fmtDate(at), to = fmtDate(until);
  if (from === NOT_REPORTED || to === NOT_REPORTED || from === to) return from === NOT_REPORTED ? to : from;
  const [fromMonth, fromDay] = from.split(" "), [toMonth, toDay] = to.split(" ");
  return fromMonth === toMonth ? `${fromMonth} ${fromDay}–${toDay}` : `${from} – ${to}`;
}
/** Age of `at` relative to `now` (ms): "9s ago", "4 min ago", "2 h ago", "3 d ago". Future times read "just now". */
export function fmtRelative(at: string | null | undefined, now: number | null | undefined): string {
  const ms = parse(at);
  if (!Number.isFinite(ms) || !finite(now)) return NOT_REPORTED;
  const seconds = Math.max(0, Math.floor((now - ms) / 1000));
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  return `${Math.floor(seconds / 86400)} d ago`;
}
/** Page meta line: "Updated Oct 1, 09:41:20 UTC · Refreshes every 15 seconds". */
export function fmtUpdated(at: string | null | undefined, refreshSeconds: number | null = 15): string {
  return `Updated ${fmtDateTime(at)}${refreshSeconds ? ` · Refreshes every ${refreshSeconds} seconds` : ""}`;
}

/* ---------- Evidence labels ---------- */

/** Badge text: "Measured · 9s ago" | "Recorded · Sep 13–14" | "Sample" | "Not reported". */
export function provenanceLabel(provenance: Provenance | null | undefined, now?: number | null): string {
  if (!provenance) return NOT_REPORTED;
  switch (provenance.kind) {
    case "measured": return provenance.at && finite(now) ? `Measured · ${fmtRelative(provenance.at, now)}` : "Measured";
    case "recorded": return provenance.at ? `Recorded · ${provenance.until ? fmtDateRange(provenance.at, provenance.until) : fmtDate(provenance.at)}` : "Recorded";
    case "sample": return "Sample";
    default: return NOT_REPORTED;
  }
}
/** Longer description for a title attribute or a context note: "Recorded Sep 14 · n = 1,666 · VERIFICATION.md §3". */
export function provenanceDetail(provenance: Provenance | null | undefined): string {
  if (!provenance) return NOT_REPORTED;
  const parts = [provenance.kind === "measured" ? "Measured on the connected device" : provenance.kind === "recorded" ? "Recorded evidence" : provenance.kind === "sample" ? "Sample value prepared for discussion; not a measurement" : NOT_REPORTED];
  if (provenance.at && provenance.kind !== "sample") parts.push(provenance.until ? fmtDateRange(provenance.at, provenance.until) : fmtDateTime(provenance.at));
  if (finite(provenance.n)) parts.push(`n = ${fmtCount(provenance.n)}`);
  if (provenance.source) parts.push(provenance.source);
  return parts.join(" · ");
}

/** "Run 23". */
export const runLabel = (run: { number: number }) => `Run ${run.number}`;

/* ---------- Statistics ---------- */

/** Median of the finite values; null when there are none. */
export function median(values: ReadonlyArray<number | null | undefined>): number | null {
  const sorted = values.filter(finite).toSorted((a, b) => a - b);
  if (!sorted.length) return null;
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}
/** Nearest-rank percentile (p in 0–1) of the finite values: in a small sample p95 can be the maximum. */
export function percentile(values: ReadonlyArray<number | null | undefined>, p: number): number | null {
  const sorted = values.filter(finite).toSorted((a, b) => a - b);
  return sorted.length ? sorted[Math.max(0, Math.ceil(sorted.length * p) - 1)] : null;
}
/** Wilson score interval (95 % by default) of a success share, 0–1. */
export function wilson(successes: number, n: number, z = 1.96): [number, number] | null {
  if (!(n > 0) || successes < 0 || successes > n) return null;
  const p = successes / n, z2 = z * z, denominator = 1 + z2 / n;
  const centre = (p + z2 / (2 * n)) / denominator;
  const half = (z * Math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))) / denominator;
  return [Math.max(0, centre - half), Math.min(1, centre + half)];
}

/* ---------- Series ---------- */

/** Expands a stored uniform series into timestamped points for charts. */
export function seriesPoints(series: SampledSeries | null | undefined): SeriesPoint[] {
  if (!series) return [];
  const start = Date.parse(series.start);
  if (!Number.isFinite(start)) return [];
  return series.values.map((value, i) => ({ at: new Date(start + i * series.stepS * 1000).toISOString(), value: finite(value) ? value : null }));
}
/** Finite range of a set of values: [low, high] or null. */
export function valueRange(values: ReadonlyArray<number | null | undefined>): [number, number] | null {
  const measured = values.filter(finite);
  return measured.length ? [Math.min(...measured), Math.max(...measured)] : null;
}
/** "45.6–58.8 °C" for a set of values, or "Not reported". */
export function fmtRange(values: ReadonlyArray<number | null | undefined>, unit: string, digits = 1): string {
  const range = valueRange(values);
  return range ? `${fmtFixed(range[0], digits)}–${fmtFixed(range[1], digits)} ${unit}` : NOT_REPORTED;
}
