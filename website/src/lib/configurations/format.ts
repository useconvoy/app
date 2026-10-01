/**
 * Formatting for the Configurations pages: en-US grouping, a space before the
 * unit ("27 %", "181 ms"), "Not reported" for a missing value (a reported zero
 * stays zero), and times in UTC.
 */
import type { SampledSeries, SeriesPoint } from "./types";

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

/* ---------- Time (UTC) ---------- */

const parse = (at: string | null | undefined) => (at ? Date.parse(at) : Number.NaN);
const day = (ms: number) => new Date(ms).toLocaleDateString("en-US", { timeZone: "UTC", month: "short", day: "numeric" });
const clock = (ms: number) => new Date(ms).toLocaleTimeString("en-US", { timeZone: "UTC", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });

/** "Oct 1, 03:59": one short value for a table cell. */
export function fmtWhen(at: string | null | undefined): string {
  const ms = parse(at);
  return Number.isFinite(ms) ? `${day(ms)}, ${clock(ms)}` : NOT_REPORTED;
}
/** "Sep 14". */
export function fmtDate(at: string | null | undefined): string {
  const ms = parse(at);
  return Number.isFinite(ms) ? day(ms) : NOT_REPORTED;
}

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
