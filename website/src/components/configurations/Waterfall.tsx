import type { TraceSpan } from "@/lib/configurations/types";
import { PathChip } from "./Badges";

const STEPS = [1, 1.2, 1.6, 2, 2.4, 3, 4, 4.8, 6, 6.4, 8];
/** Smallest "nice" axis end ≥ value whose quarters are round numbers (6,000 → 0 / 1,500 / … ; 61.3 → 64). */
export function niceAxisEnd(value: number): number {
  if (!(value > 0)) return 1;
  const power = 10 ** Math.floor(Math.log10(value));
  for (const step of STEPS) if (step * power >= value - 1e-9) return Math.round(step * power * 1000) / 1000;
  return 10 * power;
}
const pct = (value: number) => `${Math.round(value * 1000) / 1000}%`;

/**
 * Span waterfall on a shared axis from the observation: name with path chip, bar,
 * duration ("event" for an instant). Children (spans with `parentId`) indent.
 * `unit` "s" shows seconds (spans are stored in ms). Five ticks at the quarters.
 */
export function Waterfall({ spans, unit = "ms", total, axisLabel }: { spans: readonly TraceSpan[]; unit?: "ms" | "s"; total?: number; axisLabel?: string }) {
  const scale = unit === "s" ? 1000 : 1;
  const end = total ?? niceAxisEnd(Math.max(1, ...spans.map(span => (span.startMs + span.durationMs) / scale)));
  const ticks = [0, 0.25, 0.5, 0.75, 1].map(share => Math.round(share * end * 100) / 100);
  const format = (value: number) => value.toLocaleString("en-US", { maximumFractionDigits: unit === "s" ? 1 : 0 });
  return <div className="cfg-wf">
    <div className="cfg-wf__axis" aria-hidden="true"><span>{axisLabel ?? `${unit} from observation`}</span><span className="cfg-wf__ticks">{ticks.map((tick, i) => <span key={i} style={{ left: pct(i * 25) }}>{format(tick)}</span>)}</span><span /></div>
    <ol>
      {spans.map(span => {
        const start = span.startMs / scale, duration = span.durationMs / scale;
        const series = span.path === "none" ? "neutral" : span.path;
        return <li key={span.id} className={`cfg-wf__row cfg-series--${series}${span.parentId ? " cfg-wf__row--child" : ""}`}>
          <span className="cfg-wf__name">{span.name}{span.path !== "none" && <PathChip path={span.path} />}<span className="cfg-sr">, starts at {format(start)} {unit}</span></span>
          <span className="cfg-wf__track" aria-hidden="true"><span className="cfg-wf__bar" style={{ left: pct(Math.min(start / end, 1) * 100), width: pct(Math.max(Math.min(duration / end, 1) * 100, 0.4)) }} /></span>
          <span className="cfg-wf__ms">{span.durationMs ? `${format(duration)} ${unit}` : "event"}</span>
        </li>;
      })}
    </ol>
  </div>;
}
