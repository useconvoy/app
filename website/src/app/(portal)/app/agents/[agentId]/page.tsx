import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { RouteStepList } from "@/components/RouteStepList";
import { environmentsClient } from "@/lib/api/environments";
import { parseStoredSchedule } from "@/lib/agents/schedule";
import { friendlyDate, money } from "@/lib/format";
import { can } from "@/lib/permissions";
import { listRoutineBindingsForAgent } from "@/lib/routines/records";
import { startsSummary } from "@/lib/routines/summary";
import { requireWorkspacesPage } from "@/lib/workspaces/gate";

export const metadata: Metadata = { title: "Agent" };
export const dynamic = "force-dynamic";

/**
 * The Agent detail: the reusable definition (goal, instructions, budget,
 * compute, browser). Starting, scheduling, and event triggers live on the
 * Agent's routines; this page lists them and links through.
 */
export default async function AgentDetailPage({
  params,
}: {
  params: Promise<{ agentId: string }>;
}) {
  const { agentId } = await params;
  const { session, membership } = await requireWorkspacesPage();
  const client = environmentsClient();
  const agent = await client.getAgent(session.orgId, agentId);
  if (!agent) notFound();
  const [workspace, routines, workspaces] = await Promise.all([
    client.getWorkspace(session.orgId, agent.workspaceId),
    listRoutineBindingsForAgent(session.orgId, agent.id),
    client.listWorkspaces(session.orgId),
  ]);
  const workspaceNames = new Map(workspaces.map((entry) => [entry.id, entry.name]));
  const canManage = can("manage_workspaces", membership.role, membership.capabilities);

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header>
        <p className="text-xs text-muted">
          <Link href="/app/agents" className="underline">Agents</Link>
        </p>
        <h1 className="mt-1 font-display text-3xl text-ink">{agent.name}</h1>
        <p className="mt-2 text-sm text-muted">{agent.purpose}</p>
        <p className="mt-2 font-mono text-xs text-muted">
          runtime v{agent.version} · created {friendlyDate(agent.createdAt)}
        </p>
      </header>

      <section className="rounded-lg border border-line bg-card p-5">
        <div className="max-w-3xl">
          <h2 className="font-display text-lg text-ink">Goal</h2>
          <p className="mt-2 text-sm text-ink">
            {agent.automationConfigured ? agent.goal : "This Agent still needs a goal."}
          </p>
          <p className="mt-3 text-xs text-muted">{money(agent.budgetCapUsd)} per run</p>
        </div>
        {agent.planSteps.length > 0 && (
          <div className="mt-5 border-t border-line-soft pt-4">
            <h3 className="text-xs font-medium uppercase tracking-wide text-muted">Instructions</h3>
            <div className="mt-3">
              <RouteStepList
                steps={agent.planSteps.map((sentence, index) => ({
                  id: `${agent.id}-step-${index}`,
                  sentence,
                  state: "queued",
                }))}
              />
            </div>
          </div>
        )}
      </section>

      <section className="rounded-lg border border-line bg-card p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 className="font-display text-lg text-ink">Routines</h2>
            <p className="mt-2 max-w-2xl text-sm text-muted">
              A routine pairs this Agent with a Workspace and decides how it starts. Runs,
              schedules, and event triggers all live there.
            </p>
          </div>
          {canManage && agent.automationConfigured && (
            <Link
              href={`/app/routines/new?agent=${agent.id}`}
              className="rounded-md border border-line bg-card px-3 py-1.5 text-sm font-medium text-ink hover:border-pine"
            >
              New routine
            </Link>
          )}
        </div>
        {routines.length === 0 ? (
          <p className="mt-3 text-sm text-muted">
            No routines yet. This Agent cannot run until a routine pairs it with a Workspace.
          </p>
        ) : (
          <ul className="m-0 mt-4 list-none space-y-2 p-0">
            {routines.map((routine) => (
              <li key={routine.id} className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-line-soft p-3">
                <div>
                  <Link
                    href={`/app/routines/${routine.id}`}
                    className="text-sm font-medium text-ink underline-offset-2 hover:underline"
                  >
                    {routine.name}
                  </Link>
                  <p className="mt-0.5 text-xs text-muted">
                    {workspaceNames.get(routine.workspaceId) ?? "Removed workspace"}
                  </p>
                </div>
                <span className="text-xs text-muted">
                  {startsSummary(parseStoredSchedule(routine.schedule), null)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <div className="grid gap-5 md:grid-cols-2">
        <section className="rounded-lg border border-line bg-card p-5">
          <h2 className="font-display text-lg text-ink">Home workspace</h2>
          <p className="mt-2 text-sm text-muted">
            The Workspace this Agent was created with. Routines may pair it with any Workspace
            that connects the systems it uses.
          </p>
          {workspace && (
            <>
              <Link href={`/app/workspaces/${workspace.id}`} className="mt-3 inline-block text-sm text-ink underline">
                {workspace.name}
              </Link>
              <p className="mt-2 text-xs text-muted">
                {workspace.systems.map((system) => system.displayName).join(", ") || "No systems connected"}
              </p>
            </>
          )}
        </section>
        <section className="rounded-lg border border-line bg-card p-5">
          <h2 className="font-display text-lg text-ink">Compute and durable state</h2>
          <dl className="mt-3 space-y-3 text-sm">
            <div>
              <dt className="text-muted">Template</dt>
              <dd className="mt-1 font-mono text-xs text-ink">{agent.sandboxTemplate}</dd>
            </div>
            <div>
              <dt className="text-muted">Files and memory</dt>
              <dd className="mt-1 text-ink">Checkpointed on pause and restored onto fresh compute</dd>
            </div>
          </dl>
        </section>
        <section className="rounded-lg border border-line bg-card p-5">
          <h2 className="font-display text-lg text-ink">Browser</h2>
          {agent.browserPolicy ? (
            <dl className="mt-3 space-y-3 text-sm">
              <div>
                <dt className="text-muted">Allowed domains</dt>
                <dd className="mt-1 text-ink">
                  {agent.browserPolicy.allowedDomains.length > 0
                    ? agent.browserPolicy.allowedDomains.join(", ")
                    : "No domains allowed yet"}
                </dd>
              </div>
              <div>
                <dt className="text-muted">Sign-in state</dt>
                <dd className="mt-1 text-ink">
                  {agent.browserPolicy.persistProfile ? "Restored after pauses" : "Discarded with compute"}
                </dd>
              </div>
            </dl>
          ) : (
            <p className="mt-2 text-sm text-muted">This agent does not have browser access.</p>
          )}
        </section>
        <section className="rounded-lg border border-line bg-card p-5">
          <h2 className="font-display text-lg text-ink">Run behavior</h2>
          <p className="mt-2 text-sm text-muted">
            Rehearsals use the routine workspace&apos;s safe data plane; live runs use its real
            connected systems. Compute and binding details stay internal.
          </p>
        </section>
      </div>
    </div>
  );
}
