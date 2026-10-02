import type { ReactNode } from "react";
import type { SeriesPoint } from "@/lib/configurations/types";
import { Missing } from "./Badges";

/** A row of equal tiles: four across, two on phones. With a `label` the row is a named region. */
export function Tiles({ label, children }: { label?: string; children: ReactNode }) {
  return label ? <section className="cv-tiles" aria-label={label}>{children}</section> : <div className="cv-tiles">{children}</div>;
}

/** One metric: label, value with unit, one short line, and an optional sparkline. Equal size in every row. */
export function Tile({ label, value, unit, sub, chart }: { label: string; value: ReactNode | null; unit?: string; sub?: ReactNode; chart?: ReactNode }) {
  const missing = value === null || value === undefined || value === "";
  return <div className={`cv-tile${chart ? " cv-tile--chart" : ""}`} role="group" aria-label={label}>
    <p className="cv-tile__label">{label}</p>
    <p className="cv-tile__value">{missing ? <Missing /> : <>{value}{unit && <span className="cv-tile__unit">{unit}</span>}</>}</p>
    <p className="cv-tile__sub">{sub ?? " "}</p>
    {chart && <div className="cv-tile__chart">{chart}</div>}
  </div>;
}

const finite = (value: number | null | undefined): value is number => typeof value === "number" && Number.isFinite(value);
const round = (value: number) => Math.round(value * 100) / 100;

/**
 * Line and soft area of recent samples, oldest to newest (x by time when points
 * carry times). A null value breaks the line. Decorative: the value sits beside it.
 */
export function Sparkline({ points }: { points: ReadonlyArray<SeriesPoint | number | null> }) {
  const values = points.map((point, i) => typeof point === "number" || point === null ? { t: i, v: point } : { t: Date.parse(point.at), v: point.value })
    .filter(point => Number.isFinite(point.t)).toSorted((a, b) => a.t - b.t);
  const measured = values.map(point => point.v).filter(finite);
  if (measured.length < 2) return null;
  const low = Math.min(...measured), high = Math.max(...measured);
  const pad = high === low ? Math.max(Math.abs(high) * 0.1, 1) : (high - low) * 0.15;
  const lo = low - pad, hi = high + pad;
  const t0 = values[0].t, t1 = values[values.length - 1].t;
  const x = (t: number) => round(t1 === t0 ? 50 : (t - t0) / (t1 - t0) * 100);
  const y = (v: number) => round(26 - (v - lo) / (hi - lo) * 24);
  let line = "", area = "", open = false, start = 0, last = 0;
  for (const point of values) {
    if (!finite(point.v)) {
      if (open) area += `L${last},28L${start},28Z`;
      open = false;
      continue;
    }
    const px = x(point.t), py = y(point.v);
    if (!open) { start = px; area += `M${px},28L${px},${py}`; } else area += `L${px},${py}`;
    line += `${open ? "L" : "M"}${px},${py}`;
    open = true; last = px;
  }
  if (open) area += `L${last},28L${start},28Z`;
  return <svg className="cv-spark" viewBox="0 0 100 28" preserveAspectRatio="none" aria-hidden="true" focusable="false">
    <path className="cv-spark__area" d={area} />
    <path className="cv-spark__line" d={line} />
  </svg>;
}

/** Label / value pairs on one grid; every value is one line. Two columns on wide screens; `single` keeps one (a side panel). */
export function Facts({ items, single = false }: { items: ReadonlyArray<{ label: string; value: ReactNode | null }>; single?: boolean }) {
  return <dl className={`cv-facts${single ? " cv-facts--single" : ""}`}>{items.map(item => <div key={item.label}><dt>{item.label}</dt><dd>{item.value === null || item.value === "" ? <Missing /> : item.value}</dd></div>)}</dl>;
}

/** A card; with a `title` it has a heading row (`action` sits at its right). A tab's only card goes untitled: the tab names it. */
export function Card({ title, label, action, children, flush = false }: { title?: string; label?: string; action?: ReactNode; children: ReactNode; flush?: boolean }) {
  return <section className={`cv-card${flush ? " cv-card--flush" : ""}`} aria-label={title ?? label}>
    {title && <div className="cv-card__head"><h2>{title}</h2>{action}</div>}
    {children}
  </section>;
}
