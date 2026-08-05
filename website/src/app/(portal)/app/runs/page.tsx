import type { Metadata } from "next";

import { Button } from "@/components/Button";
import { ErrorBlock } from "@/components/ErrorBlock";
import { copy } from "@/lexicon";
import { listRuns } from "@/lib/api/runs";
import { can } from "@/lib/permissions";
import { startFixtureRun } from "@/lib/runs/actions";
import { requireRunPage } from "@/lib/runs/context";
import { progress } from "@/lib/runs/status";
import { isRehearsalRun } from "@/lib/routines/data";
import { RunsList, type RunListRow } from "./runs-list";

export const metadata: Metadata = { title: "Runs" };
export const dynamic = "force-dynamic";

/** The D13 on-demand seam for demos and E2E. TODO(website-W4): moves behind Routines. */
async function startDemoRun(): Promise<void> {
  "use server";
  await startFixtureRun("routine-access-review");
}

export default async function RunsPage() {
  const { membership, actor } = await requireRunPage();
  const canStart = can("trigger_production_run", membership.role, membership.capabilities);

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

  const startForm = canStart ? (
    <form action={startDemoRun}>
      <Button type="submit" variant="secondary">
        {copy.startRehearsalRun}
      </Button>
    </form>
  ) : undefined;

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl text-ink">Runs</h1>
          <p className="mt-1 text-sm text-muted">Everything your routines are doing, live.</p>
        </div>
        {startForm}
      </header>
      {rows === null ? (
        <ErrorBlock
          whatHappened="This page could not load runs."
          whatToDo="Try again in a moment."
        />
      ) : (
        <RunsList rows={rows} startAction={startForm} />
      )}
    </div>
  );
}
