import type { ReactNode } from "react";
import { fmtNumber } from "@/lib/configurations/format";
import type { Provenance } from "@/lib/configurations/types";
import { NotReported, ProvenanceBadge } from "./Badges";
import { LegendKey, type SeriesName } from "./Charts";

/** `portal-metric-grid`: 4-up by default; 3 gives two rows of three, 6 a single row of short tiles. */
export function KpiGrid({ columns = 4, children, className = "" }: { columns?: 3 | 4 | 6; children: ReactNode; className?: string }) {
  return <div className={`portal-metric-grid${columns === 4 ? "" : ` cfg-metric-grid--${columns}`}${className ? ` ${className}` : ""}`}>{children}</div>;
}

/**
 * Metric tile: label (with a series key for edge/cloud figures), mono value and
 * unit ("Not reported" when null, never 0), an optional sub-line with n and window,
 * an optional sparkline (children), and a foot with exactly one provenance badge
 * plus extras (gate badge, delta, link).
 */
export function KpiTile({ label, series, value, unit, digits = 1, sub, provenance, now, foot, children }: {
  label: ReactNode; series?: SeriesName | null; value: number | string | null; unit?: ReactNode; digits?: number; sub?: ReactNode;
  provenance: Provenance | null; now?: number | null; foot?: ReactNode; children?: ReactNode;
}) {
  const missing = value === null || (typeof value === "number" && !Number.isFinite(value));
  return <section className="portal-metric-card cfg-kpi">
    <h3>{series && <LegendKey series={series} />}{label}</h3>
    <div className="portal-metric-value">{missing ? <NotReported /> : <>{typeof value === "number" ? fmtNumber(value, digits) : value}{unit && <span>{unit}</span>}</>}</div>
    {sub && <p className="cfg-kpi__sub">{sub}</p>}
    {children}
    <div className="cfg-kpi__foot"><ProvenanceBadge provenance={provenance} now={now} />{foot}</div>
  </section>;
}

/** "↑ 1.7 points vs Run 22": good or bad tone in a tile foot. */
export function Delta({ children, tone }: { children: ReactNode; tone?: "good" | "bad" }) {
  return <span className={`cfg-delta${tone ? ` cfg-delta--${tone}` : ""}`}>{children}</span>;
}
