import type { Metadata } from "next";
import Link from "next/link";

import { ErrorBlock } from "@/components/ErrorBlock";
import { copy } from "@/lexicon";
import { isRehearsalTarget, listRuns } from "@/lib/api/runs";
import { can } from "@/lib/permissions";
import { requireRunPage } from "@/lib/runs/context";
import { RunsList, type RunListRow } from "./runs-list";

export const metadata: Metadata = { title: "Runs" };
export const dynamic = "force-dynamic";

/**
 * The runs list. Starting a run lives on its own picker page so the
 * routine, its plan, and the start affordance are seen together; the
 * header link leads there. ?mine=1 narrows the list to runs this person
 * started, which is how the account menu's "My runs" arrives here.
 */
export default async function RunsPage({
  searchParams,
}: {
  searchParams: Promise<{ mine?: string }>;
}) {
  const { session, membership, actor } = await requireRunPage();
  const { mine } = await searchParams;
  const mineOnly = mine === "1";
  const canStart = can("trigger_production_run", membership.role, membership.capabilities);

  let rows: RunListRow[] | null = null;
  try {
    const views = await listRuns(actor);
    rows = views
      .filter((view) => !mineOnly || view.started_by === session.userId)
      .map((view) => ({
        id: view.run_id,
        goal: view.goal,
        status: view.status,
        done: view.steps_done ?? 0,
        total: view.steps_total ?? 0,
        spentUsd: view.budget?.spent_usd ?? null,
        rehearsal: isRehearsalTarget(view.environment_id),
      }));
  } catch {
    rows = null;
  }

  const startLink = canStart ? (
    <Link
      href="/app/runs/new"
      className="rounded-md border border-line bg-card px-3 py-1.5 text-sm font-medium text-ink hover:border-pine"
    >
      {copy.startRun}
    </Link>
  ) : undefined;

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl text-ink">
            {mineOnly ? copy.myRunsTitle : "Runs"}
          </h1>
          <p className="mt-1 text-sm text-muted">
            {mineOnly ? copy.myRunsIntro : "Everything your Agents are doing, live."}
          </p>
          {mineOnly ? (
            <p className="mt-1 text-sm">
              <Link href="/app/runs" className="text-muted underline hover:text-ink">
                {copy.showAllRuns}
              </Link>
            </p>
          ) : null}
        </div>
        {startLink}
      </header>
      {rows === null ? (
        <ErrorBlock
          whatHappened="This page could not load runs."
          whatToDo="Try again in a moment."
        />
      ) : (
        <RunsList
          rows={rows}
          startAction={startLink}
          emptyBody={mineOnly ? copy.myRunsEmptyBody : undefined}
        />
      )}
    </div>
  );
}
