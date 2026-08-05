/**
 * Inline SVG test-score trend line: no chart dependencies, mono axis
 * labels, and a plain-sentence summary that carries the accessible
 * meaning. Scores are 0..100 on a fixed scale so trends stay comparable
 * across routines. Colors come from tokens via CSS classes only.
 */
import { friendlyDate } from "@/lib/format";
import { testScoreSummary } from "@/lexicon";

export interface TrendPoint {
  at: string;
  /** Test score, 0..100. */
  score: number;
}

/** The trend's accessible one-sentence summary, from latest and delta. */
export function trendSummary(points: TrendPoint[]): string {
  if (points.length === 0) return "No test scores yet";
  const first = points[0]!;
  const latest = points[points.length - 1]!;
  return testScoreSummary(latest.score, latest.score - first.score, points.length);
}

export interface TrendLineProps {
  /** Points in time order, oldest first. */
  points: TrendPoint[];
  /** What is being charted, for the group label, e.g. a routine name. */
  title: string;
  /** Larger drawing for detail pages. */
  large?: boolean;
}

export function TrendLine({ points, title, large = false }: TrendLineProps) {
  const summary = trendSummary(points);
  if (points.length === 0) {
    return <p className="text-sm text-muted">{summary}</p>;
  }

  const width = 240;
  const height = large ? 80 : 48;
  const padX = 4;
  const padY = 6;
  const spanX = width - padX * 2;
  const spanY = height - padY * 2;
  const x = (index: number) =>
    points.length === 1 ? padX + spanX / 2 : padX + (index / (points.length - 1)) * spanX;
  const y = (score: number) => padY + (1 - score / 100) * spanY;
  const path = points.map((point, index) => `${x(index).toFixed(1)},${y(point.score).toFixed(1)}`);
  const last = points[points.length - 1]!;

  return (
    <figure role="group" aria-label={`${title}: ${summary}`} className="m-0">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        aria-hidden="true"
        className={["block w-full max-w-md text-pine", large ? "h-24" : "h-14"].join(" ")}
        preserveAspectRatio="none"
      >
        <polyline
          points={path.join(" ")}
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <circle cx={x(points.length - 1)} cy={y(last.score)} r="3" fill="currentColor" />
      </svg>
      <div
        aria-hidden="true"
        className="mt-1 flex max-w-md items-center justify-between font-mono text-[10px] uppercase tracking-wide text-muted"
      >
        <span>{friendlyDate(points[0]!.at)}</span>
        <span>{last.score}</span>
        <span>{friendlyDate(last.at)}</span>
      </div>
      <figcaption className="mt-2 text-sm text-ink">{summary}</figcaption>
    </figure>
  );
}
