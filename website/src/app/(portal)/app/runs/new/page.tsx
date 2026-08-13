import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";

import { Button } from "@/components/Button";
import { EmptyState } from "@/components/EmptyState";
import { RouteStepList } from "@/components/RouteStepList";
import { environmentsClient } from "@/lib/api/environments";
import { money } from "@/lib/format";
import { can } from "@/lib/permissions";
import { runRoutineNow } from "@/lib/routines/actions";
import { requireRoutinesPage } from "@/lib/routines/gate";
import { listRoutineBindings } from "@/lib/routines/records";
import { copy, startRunCopy } from "@/lexicon";

export const metadata: Metadata = { title: "Start a run" };
export const dynamic = "force-dynamic";

/**
 * The start-a-run picker: the org's routines on the left, the chosen
 * routine's agent goal and instructions on the right.
 * Selection is a server-rendered query param so the page needs no client
 * state; starting goes through runRoutineNow, which re-checks permissions
 * and picks rehearsal or live exactly as the routine page's Run now does.
 */
export default async function StartRunPage({
  searchParams,
}: {
  searchParams: Promise<{ routine?: string; agent?: string }>;
}) {
  const { session, membership } = await requireRoutinesPage();
  const { routine: routineParam, agent: legacyAgentParam } = await searchParams;
  const [routines, agents, workspaces] = await Promise.all([
    listRoutineBindings(session.orgId),
    environmentsClient().listAgents(session.orgId),
    environmentsClient().listWorkspaces(session.orgId),
  ]);
  const agentsById = new Map(agents.map((agent) => [agent.id, agent]));
  const workspacesById = new Map(workspaces.map((workspace) => [workspace.id, workspace]));
  const canStart = can("trigger_production_run", membership.role, membership.capabilities);
  const canRunProduction = can("promote", membership.role, membership.capabilities);

  // Migrated routines share their agent's id, so old ?agent= links land on
  // the matching routine.
  const selectedId = routineParam ?? legacyAgentParam;
  const selected = routines.find((routine) => routine.id === selectedId) ?? null;
  const selectedAgent = selected ? agentsById.get(selected.agentId) ?? null : null;
  const selectedWorkspace = selected ? workspacesById.get(selected.workspaceId) ?? null : null;

  async function startAction(formData: FormData) {
    "use server";
    if (!selected) return;
    const target = formData.get("target") === "production" ? "production" : "rehearsal";
    const { runId } = await runRoutineNow(selected.id, target);
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
          title="No routines yet"
          body="Create a routine that pairs an Agent with a Workspace before starting a run."
          action={
            <Link
              href="/app/routines/new"
              className="rounded-md border border-line bg-card px-3 py-1.5 text-sm font-medium text-ink hover:border-pine"
            >
              New routine
            </Link>
          }
        />
      ) : (
        <div className="grid items-start gap-6 lg:grid-cols-2">
          <section aria-label="Pick a routine">
            <h2 className="text-xs font-medium uppercase tracking-wide text-muted">
              Pick a routine
            </h2>
            <ul className="m-0 mt-3 list-none space-y-2 p-0">
              {routines.map((routine) => {
                const active = routine.id === selected?.id;
                const agent = agentsById.get(routine.agentId);
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
                      <span className="mt-1 block text-sm text-muted">{agent?.goal ?? ""}</span>
                      {agent && (
                        <span className="mt-2 block font-mono text-xs uppercase text-muted">
                          {copy.upToPerRun(money(agent.budgetCapUsd))}
                        </span>
                      )}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </section>

          <section aria-label={startRunCopy.planTitle}>
            {selected && selectedAgent ? (
              <div className="rounded-lg border border-line bg-card p-5">
                <h2 className="text-base font-medium text-ink">{selected.name}</h2>
                <p className="mt-1 text-sm text-muted">{selectedAgent.goal}</p>
                <h3 className="mt-4 text-xs font-medium uppercase tracking-wide text-muted">
                  {startRunCopy.planTitle}
                </h3>
                <div className="mt-2">
                  {selectedAgent.planSteps.length > 0 ? (
                    <RouteStepList
                      steps={selectedAgent.planSteps.map((sentence, index) => ({
                        id: `${selectedAgent.id}-step-${index}`,
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
                  ) : selectedWorkspace === null ? (
                    <p className="text-sm text-muted">{startRunCopy.noWorkspaceNote}</p>
                  ) : (
                    <form action={startAction}>
                      <div className="mb-3 rounded-md border border-line-soft p-3 text-xs text-muted">
                        Agent <Link href={`/app/agents/${selectedAgent.id}`} className="font-medium text-ink underline">{selectedAgent.name}</Link>
                        {" · "}{selectedWorkspace.name} workspace
                      </div>
                      <div className="flex flex-wrap gap-2">
                        <Button type="submit" name="target" value="rehearsal">
                          {startRunCopy.startRehearsal}
                        </Button>
                        {canRunProduction && (
                          <Button type="submit" name="target" value="production" variant="secondary">
                            {startRunCopy.startProduction}
                          </Button>
                        )}
                      </div>
                      <div className="mt-2 space-y-1 text-xs text-muted">
                        <p>{copy.runNowRehearsalNote}</p>
                        {canRunProduction && <p>{copy.runNowProductionNote}</p>}
                      </div>
                    </form>
                  )}
                </div>
              </div>
            ) : (
              <div className="rounded-lg border border-line-soft bg-card px-6 py-8 text-center">
                <p className="text-sm text-muted">Pick a routine</p>
              </div>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
