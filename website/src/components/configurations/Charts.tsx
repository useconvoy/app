import Link from "next/link";
import type { ReactNode } from "react";
import { fmtFixed, fmtRange, NOT_REPORTED } from "@/lib/configurations/format";
import type { Provenance, RobotRole, SeriesPoint } from "@/lib/configurations/types";
import { ProvenanceBadge, RoleBadge } from "./Badges";
import { Icon } from "./Icons";

/** Series colour classes: one per chart container; colour is never the only cue. */
export type SeriesName = "edge" | "cloud" | "fallback" | "flag" | "operator";
const seriesClass = (series?: SeriesName | null) => series ? ` cfg-series--${series}` : "";
const num = (value: number) => String(Math.round(value * 100) / 100);
const finite = (value: number | null | undefined): value is number => typeof value === "number" && Number.isFinite(value);

/* ---------- legend ---------- */

export interface LegendItem { label: ReactNode; key?: "square" | "line" | "band" | "ref" | "operator"; series?: SeriesName }
/** A key: square (fill), line, band (p95 wash), ref (dashed threshold) or operator (outlined). */
export function LegendKey({ kind = "square", series }: { kind?: LegendItem["key"]; series?: SeriesName }) {
  return <i className={`cfg-key${kind === "square" ? "" : ` cfg-key--${kind}`}${seriesClass(series)}`} aria-hidden="true" />;
}
export function Legend({ items, className = "" }: { items: readonly LegendItem[]; className?: string }) {
  return <div className={`cfg-legend${className ? ` ${className}` : ""}`}>{items.map((item, i) => <span key={i}><LegendKey kind={item.key} series={item.series} />{item.label}</span>)}</div>;
}

/* ---------- sparkline (TelemetryChart geometry: viewBox 0 0 300 80, x 12..288, lo → y 66, hi → y 14) ---------- */

/** Auto domain like TelemetryChart: 15 % padding around the observed range, floored at 0. */
export function autoDomain(points: readonly SeriesPoint[]): [number, number] | null {
  const values = points.map(point => point.value).filter(finite);
  if (!values.length) return null;
  const low = Math.min(...values), high = Math.max(...values);
  const pad = high === low ? Math.max(Math.abs(high) * 0.05, 1) : (high - low) * 0.15;
  return [Math.max(0, low - pad), high + pad];
}

/**
 * Path strings for a sparkline. x follows time; a null value or a gap longer than
 * `maxGapMs` (default three median sample spacings) breaks the line. Every sample is a dot.
 */
export function sparkGeometry(points: readonly SeriesPoint[], domain: readonly [number, number], reference?: number | null, maxGapMs?: number): { line: string; dots: string; ref: string; count: number } {
  const timed = points.map(point => ({ t: Date.parse(point.at), value: point.value })).filter(point => Number.isFinite(point.t)).toSorted((a, b) => a.t - b.t);
  const [lo, hi] = domain;
  const span = hi - lo || 1;
  const y = (v: number) => 66 - (Math.min(Math.max(v, lo), hi) - lo) / span * 52;
  const t0 = timed[0]?.t ?? 0, t1 = timed.at(-1)?.t ?? 0;
  const x = (t: number) => (t1 === t0 ? 150 : 12 + (t - t0) / (t1 - t0) * 276);
  const steps = timed.slice(1).map((point, i) => point.t - timed[i].t).toSorted((a, b) => a - b);
  const gap = maxGapMs ?? (steps.length ? steps[Math.floor(steps.length / 2)] * 3 : Infinity);
  let line = "", dots = "", open = false, previous: number | null = null, count = 0;
  for (const point of timed) {
    if (!finite(point.value)) { open = false; continue; }
    if (open && previous !== null && point.t - previous > gap) open = false;
    const p = `${num(x(point.t))},${num(y(point.value))}`;
    line += `${open ? "L" : "M"}${p}`;
    dots += `M${p}h0`;
    open = true; previous = point.t; count++;
  }
  return { line, dots, ref: finite(reference) ? `M12,${num(y(reference))}H288` : "", count };
}

/**
 * `figure.portal-chart.cfg-spark`: sparkline with dots, optional dashed reference
 * (threshold or power cap) and a caption: the range and either the provenance
 * badge or the sample count. Small multiples pass one shared `domain`.
 */
export function Sparkline({ points, label, unit, domain, reference, series, provenance, now, caption, maxGapMs, digits = 1, empty }: {
  points: readonly SeriesPoint[]; label: string; unit: string; domain?: readonly [number, number]; reference?: number | null; series?: SeriesName | null;
  provenance?: Provenance | null; now?: number | null; caption?: ReactNode; maxGapMs?: number; digits?: number; empty?: string;
}) {
  const range = domain ?? autoDomain(points);
  const geometry = range ? sparkGeometry(points, range, reference, maxGapMs) : null;
  if (!geometry || !geometry.count) return <p className="portal-chart-empty">{empty ?? "No samples in this window."}</p>;
  return <figure className={`portal-chart cfg-spark${seriesClass(series)}`}>
    <svg viewBox="0 0 300 80" role="img" aria-label={label}>
      <path className="portal-chart-axis" d="M12 70H288" />
      {geometry.ref && <path className="cfg-spark__ref" d={geometry.ref} />}
      <path className="portal-chart-line" d={geometry.line} />
      <path className="cfg-spark__dots" d={geometry.dots} />
    </svg>
    <figcaption>{caption ?? <>{fmtRange(points.map(point => point.value), unit, digits)} {provenance ? <ProvenanceBadge provenance={provenance} now={now} /> : <span>{geometry.count} samples</span>}</>}</figcaption>
  </figure>;
}

/* ---------- small multiples ---------- */

/** Group heading with its legend (state the shared y-domain once there), then the card grid. */
export function SmallMultiples({ title, legend, wide = false, children }: { title: ReactNode; legend?: ReactNode; wide?: boolean; children: ReactNode }) {
  return <>
    <div className="cfg-subhead"><h3>{title}</h3>{legend}</div>
    <div className={`cfg-mini-grid${wide ? "" : " cfg-mini-grid--4"}`}>{children}</div>
  </>;
}

/**
 * One robot's card: name (link), role, current value, flag marker, sparkline on the
 * group's shared domain. A flagged card gets the amber line and top rule.
 * Never put measured and sample values in one card.
 */
export function MiniCard({ name, href, role, value, unit, digits = 1, flagged = false, flagLabel = "Needs attention", points, domain, reference, provenance, now, chartLabel, rangeLabel, empty }: {
  name: string; href?: string | null; role?: RobotRole; value: number | null; unit: string; digits?: number; flagged?: boolean; flagLabel?: string;
  points: readonly SeriesPoint[]; domain: readonly [number, number]; reference?: number | null; provenance: Provenance; now?: number | null; chartLabel: string; rangeLabel?: string; empty?: string;
}) {
  const geometry = sparkGeometry(points, domain, reference);
  return <article className={`cfg-mini${flagged ? " cfg-mini--flag" : ""}`}>
    <div className="cfg-mini__head">{href ? <Link className="cfg-mini__name" href={href}>{name}</Link> : <span className="cfg-mini__name">{name}</span>}{role && <RoleBadge role={role} />}</div>
    <p className="cfg-mini__value">{finite(value) ? <>{fmtFixed(value, digits)}<span>{unit}</span></> : <span className="cfg-prov cfg-prov--none">{NOT_REPORTED}</span>}
      {flagged && <span className="cfg-mini__flag"><Icon name="flag" /><span className="cfg-sr">{flagLabel}</span></span>}</p>
    {geometry.count ? <figure className={`portal-chart cfg-spark${flagged ? " cfg-series--flag" : ""}`}>
      <svg viewBox="0 0 300 80" role="img" aria-label={chartLabel}>
        <path className="portal-chart-axis" d="M12 70H288" />
        {geometry.ref && <path className="cfg-spark__ref" d={geometry.ref} />}
        <path className="portal-chart-line" d={geometry.line} />
        <path className="cfg-spark__dots" d={geometry.dots} />
      </svg>
      <figcaption>{rangeLabel ?? fmtRange(points.map(point => point.value), unit, digits)} <ProvenanceBadge provenance={provenance} now={now} /></figcaption>
    </figure> : <p className="portal-chart-empty">{empty ?? "No samples in this window."}</p>}
  </article>;
}

/* ---------- band plot (viewBox 0 0 100 100, preserveAspectRatio none, fixed height) ---------- */

export interface PlotRow { p50: number | null; p95?: number | null; event?: boolean }
/** Percent-space geometry for `cfg-plot` (the cookbook `plot()` helper). Null values are skipped. */
export function plotGeometry(rows: readonly PlotRow[], domain: readonly [number, number], ticks: readonly number[], reference?: number | null, eventsAt: "bottom" | "top" = "bottom") {
  const n = rows.length, [lo, hi] = domain;
  const X = (i: number) => (n < 2 ? 50 : i * 100 / (n - 1));
  const Y = (v: number) => 100 - (Math.min(Math.max(v, lo), hi) - lo) / ((hi - lo) || 1) * 100;
  const p50 = rows.flatMap((row, i) => finite(row.p50) ? [`${num(X(i))},${num(Y(row.p50))}`] : []);
  const hasP95 = rows.some(row => finite(row.p95));
  const p95 = hasP95 ? rows.flatMap((row, i) => finite(row.p95) ? [`${num(X(i))},${num(Y(row.p95))}`] : []) : [];
  const lastIndex = rows.findLastIndex(row => finite(row.p50));
  const last = lastIndex >= 0 ? rows[lastIndex].p50 as number : null;
  return {
    line: p50.join(" "), upper: p95.join(" "), band: p95.length ? [...p95, ...p50.toReversed()].join(" ") : "",
    grid: ticks.map(t => `M0 ${num(Y(t))}H100`).join(""),
    ticks: ticks.map(t => ({ label: t.toLocaleString("en-US"), top: `${num(Y(t))}%` })),
    ref: finite(reference) ? `M0 ${num(Y(reference))}H100` : "",
    refTop: finite(reference) ? `${num(Y(reference))}%` : null,
    end: last !== null ? { left: `${num(X(lastIndex))}%`, top: `${num(Y(last))}%` } : null,
    events: rows.map((row, i) => row.event ? (eventsAt === "top" ? `M${num(X(i))} 0V8` : `M${num(X(i))} 100V86`) : "").join(""),
  };
}

/**
 * `figure.cfg-plot`: p50 line, p50–p95 band, dashed budget line with its label,
 * event ticks (e.g. hours with fallback), y ticks and evenly spaced x labels
 * (≤ 6 characters; put dates in `note`). Two plots side by side go in <PlotPair>.
 */
export function BandPlot({ title, series, rows, domain, ticks, reference, referenceLabel, xLabels, legend, note, provenance, now, label, small = false, eventsAt = "bottom", cursor }: {
  title: ReactNode; series?: SeriesName | null; rows: readonly PlotRow[]; domain: readonly [number, number]; ticks: readonly number[];
  reference?: number | null; referenceLabel?: string; xLabels: readonly string[]; legend?: readonly LegendItem[]; note?: ReactNode; provenance?: Provenance | null; now?: number | null;
  label: string; small?: boolean; eventsAt?: "bottom" | "top"; cursor?: number | null;
}) {
  const g = plotGeometry(rows, domain, ticks, reference, eventsAt);
  return <figure className={`cfg-plot${small ? " cfg-plot--sm" : ""}${seriesClass(series)}`}>
    <figcaption className="cfg-plot__head"><span className="cfg-plot__title">{series && series !== "flag" && <LegendKey series={series} />}{title}</span>{provenance && <ProvenanceBadge provenance={provenance} now={now} />}</figcaption>
    <div className="cfg-plot__body">
      <div className="cfg-plot__y" aria-hidden="true">{g.ticks.map(tick => <span key={tick.label} style={{ top: tick.top }}>{tick.label}</span>)}</div>
      <div className="cfg-plot__frame">
        <svg className="cfg-plot__svg" viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label={label}>
          <path className="cfg-plot__grid" d={g.grid} />
          {g.band && <polygon className="cfg-plot__band" points={g.band} />}
          <polyline className="cfg-plot__line" points={g.line} />
          {g.ref && <path className="cfg-plot__ref" d={g.ref} />}
          {g.events && <path className="cfg-plot__events" d={g.events} />}
        </svg>
        {g.refTop && referenceLabel && <span className="cfg-plot__ref-label" style={{ top: g.refTop }}>{referenceLabel}</span>}
        {g.end && <span className="cfg-plot__end" style={{ left: g.end.left, top: g.end.top }} />}
        {finite(cursor) && <span className="cfg-plot__cursor" style={{ left: `${num(Math.min(Math.max(cursor, 0), 1) * 100)}%` }} />}
      </div>
    </div>
    {xLabels.length > 0 && <div className="cfg-plot__x" aria-hidden="true">{xLabels.map((text, i) => <span key={`${i}-${text}`} style={{ left: `${num(xLabels.length < 2 ? 0 : i / (xLabels.length - 1) * 100)}%` }}>{text}</span>)}</div>}
    {legend && legend.length > 0 && <Legend items={legend} className="cfg-plot__legend" />}
    {note && <p className="cfg-plot__note">{note}</p>}
  </figure>;
}
export function PlotPair({ children }: { children: ReactNode }) { return <div className="cfg-plot-pair">{children}</div>; }
