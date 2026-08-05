import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { ScorecardTable } from "@/components/ScorecardTable";
import { TrajectoryList } from "@/components/TrajectoryList";
import { TrendLine } from "@/components/TrendLine";
import { evalsClient } from "@/lib/api/evals";
import { routines } from "@/lib/fixtures/world";
import { requireRoutinesPage } from "@/lib/routines/gate";
import { improveCopy } from "@/lexicon";

export const metadata: Metadata = { title: "Routine evaluation" };

/**
 * Per-routine evaluation detail: the trend large, per-run scorecards with
 * labeled pass/fail criteria, and the trajectories behind them with
 * rehearsal rows in the pencil treatment (law 2). All reads go through the
 * typed evals client. TODO(evals).
 */
export default async function EvaluationDetailPage({
  params,
}: {
  params: Promise<{ routineId: string }>;
}) {
  const { routineId } = await params;
  await requireRoutinesPage();
  const routine = routines.find((candidate) => candidate.id === routineId);
  if (!routine) notFound();

  const client = evalsClient();
  const [trend, trajectories] = await Promise.all([
    client.scoreTrend(routineId),
    client.trajectories(routineId),
  ]);
  const scorecards = (
    await Promise.all(
      trajectories.map(async (trajectory) => ({
        trajectory,
        scorecard: await client.scorecard(trajectory.runId),
      })),
    )
  ).filter((entry) => entry.scorecard !== null);

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

      <section aria-label={improveCopy.trajectoriesTitle}>
        <h2 className="font-display text-lg text-ink">{improveCopy.trajectoriesTitle}</h2>
        <div className="mt-3">
          <TrajectoryList items={trajectories} />
        </div>
      </section>

      <section aria-label={improveCopy.scorecardsTitle}>
        <h2 className="font-display text-lg text-ink">{improveCopy.scorecardsTitle}</h2>
        <div className="mt-3 space-y-4">
          {scorecards.map(({ trajectory, scorecard }) => (
            <ScorecardTable
              key={trajectory.runId}
              scorecard={scorecard!}
              headline={trajectory.headline}
              at={trajectory.at}
              rehearsal={trajectory.sandbox}
            />
          ))}
        </div>
      </section>
    </div>
  );
}
