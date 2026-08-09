import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";

import { Button } from "@/components/Button";
import { EmptyState } from "@/components/EmptyState";
import { RouteStepList } from "@/components/RouteStepList";
import { money } from "@/lib/format";
import { can } from "@/lib/permissions";
import { runRoutineNow } from "@/lib/routines/actions";
import { environmentsClient } from "@/lib/api/environments";
import { resolveWorkspace } from "@/lib/routines/data";
import { requireRoutinesPage } from "@/lib/routines/gate";
import { listRoutines } from "@/lib/routines/queries";
import { copy, startRunCopy } from "@/lexicon";

export const metadata: Metadata = { title: "Start a run" };
export const dynamic = "force-dynamic";

/**
 * The start-a-run picker: the org's routines on the left, the chosen
 * routine's plan and its clearly labeled start affordance on the right.
 * Selection is a server-rendered query param so the page needs no client
 * state; starting goes through runRoutineNow, which re-checks permissions
 * and picks rehearsal or live exactly as the routine page's Run now does.
 */
export default async function StartRunPage({
  searchParams,
}: {
  searchParams: Promise<{ routine?: string }>;
}) {
  const { session, membership } = await requireRoutinesPage();
  const { routine: routineParam } = await searchParams;
  const routines = await listRoutines(session.orgId);
  const canStart = can("trigger_production_run", membership.role, membership.capabilities);
  const production = can("promote", membership.role, membership.capabilities);

  const selected = routines.find((routine) => routine.id === routineParam) ?? null;
  const workspace = selected ? await resolveWorkspace(session.orgId, selected) : null;
  const executionEnvironments = workspace
    ? await environmentsClient().listExecutionEnvironments(session.orgId, workspace.id)
    : [];

  async function startAction(formData: FormData) {
    "use server";
    if (!selected) return;
    const environmentId = formData.get("environmentId");
    const { runId } = await runRoutineNow(
      selected.id,
      typeof environmentId === "string" && environmentId ? environmentId : undefined,
    );
    redirect(`/app/runs/${runId}`);
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header>
        <p className="text-xs text-muted">
          <Link href="/app/runs" className="underline">
            Runs
          </Link>
        </p>
        <h1 className="mt-1 font-display text-3xl text-ink">{startRunCopy.title}</h1>
        <p className="mt-2 text-sm text-muted">{startRunCopy.intro}</p>
      </header>

      {routines.length === 0 ? (
        <EmptyState
          title={startRunCopy.noRoutinesTitle}
          body={startRunCopy.noRoutinesBody}
          action={
            <Link
              href="/app/catalog"
              className="rounded-md border border-line bg-card px-3 py-1.5 text-sm font-medium text-ink hover:border-pine"
            >
              {startRunCopy.browseCatalog}
            </Link>
          }
        />
      ) : (
        <div className="grid items-start gap-6 lg:grid-cols-2">
          <section aria-label={startRunCopy.pickRoutine}>
            <h2 className="text-xs font-medium uppercase tracking-wide text-muted">
              {startRunCopy.pickRoutine}
            </h2>
            <ul className="m-0 mt-3 list-none space-y-2 p-0">
              {routines.map((routine) => {
                const active = routine.id === selected?.id;
                return (
                  <li key={routine.id}>
                    <Link
                      href={`/app/runs/new?routine=${routine.id}`}
                      aria-current={active ? "true" : undefined}
                      className={[
                        "block rounded-lg border bg-card p-4 hover:border-pine",
                        active ? "border-pine" : "border-line",
                      ].join(" ")}
                    >
                      <span className="block text-sm font-medium text-ink">{routine.name}</span>
                      <span className="mt-1 block text-sm text-muted">{routine.descriptor}</span>
                      <span className="mt-2 block font-mono text-xs uppercase text-muted">
                        {copy.upToPerRun(money(routine.budgetCapUsd))}
                      </span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </section>

          <section aria-label={startRunCopy.planTitle}>
            {selected ? (
              <div className="rounded-lg border border-line bg-card p-5">
                <h2 className="text-base font-medium text-ink">{selected.name}</h2>
                <p className="mt-1 text-sm text-muted">{selected.descriptor}</p>
                <h3 className="mt-4 text-xs font-medium uppercase tracking-wide text-muted">
                  {startRunCopy.planTitle}
                </h3>
                <div className="mt-2">
                  {selected.planSteps.length > 0 ? (
                    <RouteStepList
                      steps={selected.planSteps.map((sentence, index) => ({
                        id: `${selected.id}-step-${index}`,
                        sentence,
                        state: "queued",
                      }))}
                    />
                  ) : (
                    <p className="text-sm text-muted">{copy.planEmptyNote}</p>
                  )}
                </div>
                <div className="mt-5 border-t border-line-soft pt-4">
                  {!canStart ? (
                    <p className="text-sm text-muted">{startRunCopy.viewersCannotStart}</p>
                  ) : workspace === null ? (
                    <p className="text-sm text-muted">{startRunCopy.noWorkspaceNote}</p>
                  ) : (
                    <form action={startAction}>
                      {executionEnvironments.length > 0 && (
                        <label className="mb-3 block text-xs text-muted">
                          Environment
                          <select
                            name="environmentId"
                            defaultValue={
                              executionEnvironments.find((environment) => environment.isDefault)?.id ??
                              executionEnvironments[0]?.id
                            }
                            className="mt-1 block w-full rounded-md border border-line bg-card px-2 py-1.5 text-sm text-ink"
                          >
                            {executionEnvironments.map((environment) => (
                              <option key={environment.id} value={environment.id}>
                                {environment.name}{environment.isDefault ? " (default)" : ""}
                              </option>
                            ))}
                          </select>
                        </label>
                      )}
                      <Button type="submit">
                        {production ? startRunCopy.startProduction : startRunCopy.startRehearsal}
                      </Button>
                      <p className="mt-2 text-xs text-muted">
                        {production ? copy.runNowProductionNote : copy.runNowRehearsalNote}
                      </p>
                    </form>
                  )}
                </div>
              </div>
            ) : (
              <div className="rounded-lg border border-line-soft bg-card px-6 py-8 text-center">
                <p className="text-sm text-muted">{startRunCopy.pickRoutine}</p>
              </div>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
