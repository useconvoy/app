"use client";

import { useId } from "react";

export type ChartPoint = { at: string; value: number | null };
export function number(value: number | null | undefined, digits = 1): string {
  return value == null || !Number.isFinite(value) ? "Not reported" : value.toLocaleString("en-US", { maximumFractionDigits: digits });
}
export function timestamp(value: string | null | undefined): string {
  if (!value || !Number.isFinite(Date.parse(value))) return "Not reported";
  return new Date(value).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit", timeZoneName: "short" });
}

/** Missing measurements and report gaps split the path; every dot is a reported sample. */
export function TelemetryChart({ title, unit, points, maxGapMs }: { title: string; unit: string; points: ChartPoint[]; maxGapMs: number }) {
  const id = useId();
  const sorted = points.filter((p) => Number.isFinite(Date.parse(p.at))).toSorted((a, b) => Date.parse(a.at) - Date.parse(b.at));
  const measured = sorted.filter((p): p is ChartPoint & { value: number } => p.value !== null && Number.isFinite(p.value));
  if (!measured.length) return <div className="portal-chart-empty">No {title.toLowerCase()} measurements in this sample window.</div>;
  const start = Date.parse(sorted[0].at);
  const end = Date.parse(sorted.at(-1)!.at);
  const low = Math.min(...measured.map((p) => p.value));
  const high = Math.max(...measured.map((p) => p.value));
  const padding = high === low ? Math.max(Math.abs(high) * .05, 1) : (high - low) * .15;
  const min = Math.max(0, low - padding);
  const max = high + padding;
  const x = (at: string) => end === start ? 150 : 12 + ((Date.parse(at) - start) / (end - start)) * 276;
  const y = (v: number) => 66 - (v - min) / (max - min) * 52;
  const paths: string[] = [];
  let current = "";
  let previous: ChartPoint | null = null;
  for (const p of sorted) {
    if (p.value === null || !Number.isFinite(p.value)) { if (current) paths.push(current); current = ""; previous = null; continue; }
    if (previous && Date.parse(p.at) - Date.parse(previous.at) > maxGapMs) { if (current) paths.push(current); current = ""; }
    current += `${current ? " L" : "M"}${x(p.at).toFixed(2)},${y(p.value).toFixed(2)}`;
    previous = p;
  }
  if (current) paths.push(current);
  return <figure className="portal-chart">
    <svg viewBox="0 0 300 80" role="img" aria-labelledby={`${id}-title ${id}-desc`}>
      <title id={`${id}-title`}>{title} measurements</title>
      <desc id={`${id}-desc`}>{measured.length} samples, {number(low)} to {number(high)} {unit}. {timestamp(sorted[0].at)} through {timestamp(sorted.at(-1)!.at)}. Gaps are not filled. Exact values are in the telemetry sample table.</desc>
      <path d="M12 70H288" className="portal-chart-axis" />
      {paths.map((d, i) => <path key={i} d={d} className="portal-chart-line" />)}
      {measured.map((p, i) => <circle key={`${p.at}-${i}`} cx={x(p.at)} cy={y(p.value)} r="1.8" className="portal-chart-dot" />)}
    </svg>
    <figcaption>{number(low)}–{number(high)} {unit} <span>{measured.length} measured samples</span></figcaption>
  </figure>;
}
