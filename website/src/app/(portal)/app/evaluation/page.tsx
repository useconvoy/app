import type { Metadata } from "next";
import Link from "next/link";

import { TrendLine } from "@/components/TrendLine";
import { evalsClient } from "@/lib/api/evals";
import { routines } from "@/lib/fixtures/world";
import { requireRoutinesPage } from "@/lib/routines/gate";
import { improveCopy } from "@/lexicon";

export const metadata: Metadata = { title: "Routine evaluation" };

/**
 * Routine evaluation: per-routine test-score trends and the
 * scenario suites behind them, read by every role. Data comes through the
 * typed evals client; the fixture adapter serves it until the service is
 * live. TODO(evals).
 */
export default async function EvaluationPage() {
  await requireRoutinesPage();
  const client = evalsClient();
  const cards = await Promise.all(
    routines.map(async (routine) => ({
      routine,
      trend: await client.scoreTrend(routine.id),
      suites: await client.listSuites(routine.id),
    })),
  );

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header>
        <h1 className="font-display text-3xl text-ink">{improveCopy.evaluationTitle}</h1>
        <p className="mt-2 text-sm text-muted">{improveCopy.evaluationIntro}</p>
      </header>
      <div className="space-y-4">
        {cards.map(({ routine, trend, suites }) => (
          <section
            key={routine.id}
            aria-label={routine.name}
            className="rounded-lg border border-line bg-card p-5"
          >
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div className="min-w-64 flex-1">
                <h2 className="text-base font-medium text-ink">
                  <Link href={`/app/evaluation/${routine.id}`} className="underline">
                    {routine.name}
                  </Link>
                </h2>
                <div className="mt-3">
                  <TrendLine points={trend} title={routine.name} />
                </div>
              </div>
              <div className="w-56">
                <h3 className="text-xs font-medium uppercase tracking-wide text-muted">
                  {improveCopy.suitesTitle}
                </h3>
                <ul className="m-0 mt-2 list-none space-y-1.5 p-0">
                  {suites.map((suite) => (
                    <li key={suite.id} className="text-sm text-ink">
                      {suite.name}
                      <span className="ml-2 font-mono text-xs text-muted">
                        {improveCopy.scenarioCount(suite.scenarioCount)}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}
