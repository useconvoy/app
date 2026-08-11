import type { Metadata } from "next";
import Link from "next/link";

import { EmptyState } from "@/components/EmptyState";
import { TrendLine } from "@/components/TrendLine";
import { scoreTrend } from "@/lib/api/evals";
import { requireRoutinesPage } from "@/lib/routines/gate";
import { listRoutines } from "@/lib/routines/queries";
import { improveCopy } from "@/lexicon";

export const metadata: Metadata = { title: "Agent evaluation" };
export const dynamic = "force-dynamic";

/**
 * Routine evaluation: per-routine test-score trends over the org's real
 * score history, read by every role. A fresh organization has no routines
 * and sees an honest empty state; a routine without scores charts nothing
 * and says so.
 */
export default async function EvaluationPage() {
  const { session } = await requireRoutinesPage();
  const routines = await listRoutines(session.orgId);
  const cards = await Promise.all(
    routines.map(async (routine) => ({
      routine,
      trend: await scoreTrend(session.orgId, routine.id),
    })),
  );

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header>
        <h1 className="font-display text-3xl text-ink">{improveCopy.evaluationTitle}</h1>
        <p className="mt-2 text-sm text-muted">{improveCopy.evaluationIntro}</p>
      </header>
      {cards.length === 0 ? (
        <EmptyState
          title={improveCopy.evaluationEmptyTitle}
          body={improveCopy.evaluationEmptyBody}
        />
      ) : (
        <div className="space-y-4">
          {cards.map(({ routine, trend }) => (
            <section
              key={routine.id}
              aria-label={routine.name}
              className="rounded-lg border border-line bg-card p-5"
            >
              <h2 className="text-base font-medium text-ink">
                <Link href={`/app/evaluation/${routine.id}`} className="underline">
                  {routine.name}
                </Link>
              </h2>
              <div className="mt-3">
                <TrendLine points={trend} title={routine.name} />
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
