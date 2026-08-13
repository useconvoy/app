import type { Metadata } from "next";
import Link from "next/link";

import { EmptyState } from "@/components/EmptyState";
import { StatusChip } from "@/components/StatusChip";
import { environmentsClient, listEventRules } from "@/lib/api/environments";
import { isRehearsalTarget, listRuns } from "@/lib/api/runs";
import { parseStoredSchedule } from "@/lib/agents/schedule";
import { orgTenantId } from "@/lib/agents/queries";
import { requireRoutinesPage } from "@/lib/routines/gate";
import { listRoutineBindings } from "@/lib/routines/records";
import { startsSummary } from "@/lib/routines/summary";
import { can } from "@/lib/permissions";

export const metadata: Metadata = { title: "Routines" };
export const dynamic = "force-dynamic";

/**
 * The routines list: each row is one Agent bound to one Workspace with how
 * it starts. The Starts column is the schedule overview, so cadences are
 * visible without opening each routine.
 */
export default async function RoutinesPage() {
  const { session, membership } = await requireRoutinesPage();
  const canManage = can("manage_workspaces", membership.role, membership.capabilities);
  const [routines, agents, workspaces] = await Promise.all([
    listRoutineBindings(session.orgId),
    environmentsClient().listAgents(session.orgId),
    environmentsClient().listWorkspaces(session.orgId),
  ]);
  const agentNames = new Map(agents.map((agent) => [agent.id, agent.name]));
  const workspaceNames = new Map(workspaces.map((workspace) => [workspace.id, workspace.name]));

  // Event rules and run history both degrade to unknown when their service
  // is unreachable; the list itself stays up.
  const eventRuleCounts = new Map<string, number>();
  let rulesKnown = false;
  try {
    const rules = await listEventRules(session.orgId);
    if (rules !== null) {
      rulesKnown = true;
      for (const rule of rules) {
        eventRuleCounts.set(rule.agentId, (eventRuleCounts.get(rule.agentId) ?? 0) + 1);
      }
    }
  } catch {
    rulesKnown = false;
  }
  const latestRun = new Map<string, { status: string; rehearsal: boolean }>();
  try {
    const actor = { actorId: session.userId, tenantId: await orgTenantId(session.orgId) };
    for (const run of await listRuns(actor)) {
      if (!run.agent_id || latestRun.has(run.agent_id)) continue;
      latestRun.set(run.agent_id, {
        status: run.status,
        rehearsal: isRehearsalTarget(run.environment_id),
      });
    }
  } catch {
    // The control plane may be unreachable; the column renders empty.
  }

  const newRoutine = canManage ? (
    <Link
      href="/app/routines/new"
      className="rounded-md border border-line bg-card px-3 py-1.5 text-sm font-medium text-ink hover:border-pine"
    >
      New routine
    </Link>
  ) : undefined;

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl text-ink">Routines</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted">
            A routine pairs an Agent with a Workspace and decides how it starts: on a schedule,
            from an event, or by hand. The same Agent can run through several routines.
          </p>
        </div>
        {newRoutine}
      </header>
      {routines.length === 0 ? (
        <EmptyState
          title="No routines yet"
          body="Pick an Agent and a Workspace to create the first one."
          action={newRoutine}
        />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-separate border-spacing-0 rounded-lg border border-line bg-card text-sm">
            <thead>
              <tr className="text-left font-mono text-xs uppercase tracking-wide text-muted">
                <th className="border-b border-line px-4 py-2 font-medium">Routine</th>
                <th className="border-b border-line px-4 py-2 font-medium">Agent</th>
                <th className="border-b border-line px-4 py-2 font-medium">Workspace</th>
                <th className="border-b border-line px-4 py-2 font-medium">Starts</th>
                <th className="border-b border-line px-4 py-2 font-medium">Last run</th>
              </tr>
            </thead>
            <tbody>
              {routines.map((routine) => {
                const schedule = parseStoredSchedule(routine.schedule);
                const latest = latestRun.get(routine.id);
                return (
                  <tr key={routine.id}>
                    <td className="border-b border-line-soft px-4 py-3">
                      <Link
                        href={`/app/routines/${routine.id}`}
                        className="text-ink underline-offset-2 hover:underline"
                      >
                        {routine.name}
                      </Link>
                    </td>
                    <td className="border-b border-line-soft px-4 py-3 text-muted">
                      {agentNames.get(routine.agentId) ?? "Removed agent"}
                    </td>
                    <td className="border-b border-line-soft px-4 py-3 text-muted">
                      {workspaceNames.get(routine.workspaceId) ?? "Removed workspace"}
                    </td>
                    <td className="border-b border-line-soft px-4 py-3 text-muted">
                      {startsSummary(
                        schedule,
                        rulesKnown ? eventRuleCounts.get(routine.id) ?? 0 : null,
                      )}
                    </td>
                    <td className="border-b border-line-soft px-4 py-3">
                      {latest ? (
                        <StatusChip status={latest.status} rehearsal={latest.rehearsal} />
                      ) : (
                        <span className="text-xs text-muted">None yet</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
