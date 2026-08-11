import type { Metadata } from "next";
import Link from "next/link";
import { notFound, redirect } from "next/navigation";

import { Button } from "@/components/Button";
import { RouteStepList } from "@/components/RouteStepList";
import { environmentsClient } from "@/lib/api/environments";
import { runAgentNow } from "@/lib/agents/run-actions";
import { friendlyDate, money } from "@/lib/format";
import { can } from "@/lib/permissions";
import { requireWorkspacesPage } from "@/lib/workspaces/gate";

export const metadata: Metadata = { title: "Agent" };
export const dynamic = "force-dynamic";

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
  const workspace = await client.getWorkspace(session.orgId, agent.workspaceId);
  const canStart = can("trigger_production_run", membership.role, membership.capabilities);
  const canRunProduction = can("promote", membership.role, membership.capabilities);

  async function startAction(formData: FormData) {
    "use server";
    const target = formData.get("target") === "production" ? "production" : "rehearsal";
    const { runId } = await runAgentNow(agentId, target);
    redirect(`/app/runs/${runId}`);
  }

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
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="max-w-3xl">
            <h2 className="font-display text-lg text-ink">Goal</h2>
            <p className="mt-2 text-sm text-ink">
              {agent.automationConfigured ? agent.goal : "This Agent still needs a goal."}
            </p>
            <p className="mt-3 text-xs text-muted">
              {agent.scheduleDescription || "Starts on demand"} · {money(agent.budgetCapUsd)} per run
            </p>
          </div>
          {canStart && agent.automationConfigured && workspace && (
            <form action={startAction} className="flex flex-wrap gap-2">
              <Button type="submit" name="target" value="rehearsal">Run rehearsal</Button>
              {canRunProduction && (
                <Button type="submit" name="target" value="production" variant="secondary">
                  Run live
                </Button>
              )}
            </form>
          )}
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

      <div className="grid gap-5 md:grid-cols-2">
        <section className="rounded-lg border border-line bg-card p-5">
          <h2 className="font-display text-lg text-ink">Workspace and systems</h2>
          <p className="mt-2 text-sm text-muted">
            This Agent uses the systems and permissions configured in a shared Workspace.
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
            Rehearsals use the Workspace&apos;s safe data plane; live runs use its real connected systems. Compute and binding details stay internal.
          </p>
        </section>
      </div>
    </div>
  );
}
