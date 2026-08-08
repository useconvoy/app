import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { EmptyState } from "@/components/EmptyState";
import { TrendLine } from "@/components/TrendLine";
import { scoredRuns, scoreTrend } from "@/lib/api/evals";
import { friendlyDateTime } from "@/lib/format";
import { requireRoutinesPage } from "@/lib/routines/gate";
import { getRoutine } from "@/lib/routines/queries";
import { improveCopy } from "@/lexicon";

export const metadata: Metadata = { title: "Routine evaluation" };
export const dynamic = "force-dynamic";

/**
 * Per-routine evaluation detail: the trend large over the routine's real
 * score history, and the scored runs behind it, newest first. Scorecards
 * with per-criterion detail arrive with the evaluation service; until
 * then this page shows only what is actually recorded.
 */
export default async function EvaluationDetailPage({
  params,
}: {
  params: Promise<{ routineId: string }>;
}) {
  const { routineId } = await params;
  const { session } = await requireRoutinesPage();
  const routine = await getRoutine(session.orgId, routineId);
  if (!routine) notFound();

  const [trend, scored] = await Promise.all([
    scoreTrend(session.orgId, routineId),
    scoredRuns(session.orgId, routineId),
  ]);

  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <header>
        <p className="text-xs text-muted">
          <Link href="/app/evaluation" className="underline">
            {improveCopy.evaluationTitle}
          </Link>
        </p>
        <h1 className="mt-1 font-display text-3xl text-ink">{routine.name}</h1>
        <p className="mt-2 text-sm text-muted">{routine.descriptor}</p>
      </header>

      <section aria-label={improveCopy.trendTitle}>
        <h2 className="font-display text-lg text-ink">{improveCopy.trendTitle}</h2>
        <div className="mt-3 rounded-lg border border-line bg-card p-5">
          <TrendLine points={trend} title={routine.name} large />
        </div>
      </section>

      <section aria-label={improveCopy.scoredRunsTitle}>
        <h2 className="font-display text-lg text-ink">{improveCopy.scoredRunsTitle}</h2>
        <div className="mt-3">
          {scored.length > 0 ? (
            <table className="w-full border-separate border-spacing-0 rounded-lg border border-line bg-card text-sm">
              <thead>
                <tr className="text-left font-mono text-xs uppercase tracking-wide text-muted">
                  <th className="border-b border-line px-4 py-2 font-medium">
                    {improveCopy.runColumn}
                  </th>
                  <th className="border-b border-line px-4 py-2 font-medium">
                    {improveCopy.scoreColumn}
                  </th>
                  <th className="border-b border-line px-4 py-2 font-medium">
                    {improveCopy.scoredAtColumn}
                  </th>
                </tr>
              </thead>
              <tbody>
                {scored.map((run) => (
                  <tr key={`${run.runRef}:${run.recordedAt}`}>
                    <td className="border-b border-line-soft px-4 py-3">
                      <Link
                        href={`/app/runs/${run.runRef}`}
                        className="text-ink underline-offset-2 hover:underline"
                      >
                        {routine.name}
                      </Link>
                    </td>
                    <td className="border-b border-line-soft px-4 py-3 font-mono text-xs text-ink">
                      {run.score}
                    </td>
                    <td className="border-b border-line-soft px-4 py-3 font-mono text-xs text-muted">
                      {friendlyDateTime(run.recordedAt)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <EmptyState
              title={improveCopy.noScoresYet}
              body={improveCopy.scoredRunsEmptyBody}
            />
          )}
        </div>
      </section>
    </div>
  );
}
