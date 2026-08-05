/**
 * Per-run scorecard: the criteria behind one run's test score, each with a
 * labeled pass/fail chip (text plus color, never color alone) and a plain
 * note. The score itself is a fact from the log, so it renders mono.
 */
import { Chip } from "@/components/Chip";
import type { RunScorecard } from "@/lib/api/evals";
import { friendlyDateTime } from "@/lib/format";
import { terms } from "@/lexicon";

export interface ScorecardTableProps {
  scorecard: RunScorecard;
  /** Plain sentence describing the run, from its trajectory. */
  headline: string;
  at?: string;
  /** Rehearsal runs carry the pencil treatment: graphite, dashed, labeled. */
  rehearsal?: boolean;
}

export function ScorecardTable({ scorecard, headline, at, rehearsal = false }: ScorecardTableProps) {
  return (
    <section
      aria-label={headline}
      className={[
        "rounded-lg border bg-card p-5",
        rehearsal ? "border-dashed border-graphite" : "border-line",
      ].join(" ")}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-medium text-ink">{headline}</h3>
          {at && <p className="mt-0.5 font-mono text-xs uppercase text-muted">{friendlyDateTime(at)}</p>}
        </div>
        <div className="flex items-center gap-2">
          {rehearsal && (
            <Chip tone="graphite" dashed mono>
              {terms.sandbox}
            </Chip>
          )}
          <span className="font-mono text-lg text-ink">{scorecard.score}</span>
        </div>
      </div>
      <table className="mt-3 w-full border-collapse text-sm">
        <thead>
          <tr className="text-left text-xs font-medium uppercase tracking-wide text-muted">
            <th scope="col" className="py-1.5 pr-3 font-medium">
              What was checked
            </th>
            <th scope="col" className="py-1.5 pr-3 font-medium">
              Result
            </th>
            <th scope="col" className="py-1.5 font-medium">
              Note
            </th>
          </tr>
        </thead>
        <tbody>
          {scorecard.criteria.map((criterion) => (
            <tr key={criterion.name} className="border-t border-line-soft align-top">
              <td className="py-2 pr-3 text-ink">{criterion.name}</td>
              <td className="py-2 pr-3">
                <Chip tone={criterion.pass ? "pass" : "fail"}>{criterion.pass ? "Pass" : "Fail"}</Chip>
              </td>
              <td className="py-2 text-muted">{criterion.note}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
