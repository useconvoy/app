import type { Metadata } from "next";

import { ErrorBlock } from "@/components/ErrorBlock";
import { listRuns } from "@/lib/api/runs";
import { requireRunPage } from "@/lib/runs/context";
import { progress } from "@/lib/runs/status";
import { isRehearsalRun } from "@/lib/routines/data";
import { RunsList, type RunListRow } from "./runs-list";

export const metadata: Metadata = { title: "Runs" };
export const dynamic = "force-dynamic";

/**
 * The runs list. On-demand starts live on the routine surfaces (Run now),
 * where the routine's plan and budget are in view.
 */
export default async function RunsPage() {
  const { actor } = await requireRunPage();

  let rows: RunListRow[] | null = null;
  try {
    const views = await listRuns(actor);
    rows = views.map((view) => {
      const { done, total } = progress(view.steps ?? []);
      return {
        id: view.run_id,
        goal: view.goal,
        status: view.status,
        done,
        total,
        spentUsd: view.budget?.spent_usd ?? null,
        rehearsal: isRehearsalRun(view.run_id),
      };
    });
  } catch {
    rows = null;
  }

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <header>
        <h1 className="font-display text-3xl text-ink">Runs</h1>
        <p className="mt-1 text-sm text-muted">Everything your routines are doing, live.</p>
      </header>
      {rows === null ? (
        <ErrorBlock
          whatHappened="This page could not load runs."
          whatToDo="Try again in a moment."
        />
      ) : (
        <RunsList rows={rows} />
      )}
    </div>
  );
}
