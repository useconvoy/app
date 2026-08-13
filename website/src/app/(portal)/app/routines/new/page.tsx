import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";

import { Button } from "@/components/Button";
import { EmptyState } from "@/components/EmptyState";
import { environmentsClient } from "@/lib/api/environments";
import { money } from "@/lib/format";
import { can } from "@/lib/permissions";
import { createRoutine } from "@/lib/routines/actions";
import { requireRoutinesPage } from "@/lib/routines/gate";
import { missingSystems, systemDisplayNames } from "@/lib/workspaces/fit";

export const metadata: Metadata = { title: "New routine" };
export const dynamic = "force-dynamic";

/**
 * The three-step routine builder: pick an Agent, pick a Workspace whose
 * connectors cover what that Agent uses, name the pairing. Selection is
 * server-rendered query state, like the start-a-run picker. How the
 * routine starts is configured on its page after creation; new routines
 * begin on demand.
 */
export default async function NewRoutinePage({
  searchParams,
}: {
  searchParams: Promise<{ agent?: string; workspace?: string }>;
}) {
  const { session, membership } = await requireRoutinesPage();
  if (!can("manage_workspaces", membership.role, membership.capabilities)) {
    redirect("/app/routines");
  }
  const { agent: agentParam, workspace: workspaceParam } = await searchParams;
  const [agents, workspaces] = await Promise.all([
    environmentsClient().listAgents(session.orgId),
    environmentsClient().listWorkspaces(session.orgId),
  ]);
  const configured = agents.filter((agent) => agent.automationConfigured);
  const selectedAgent = configured.find((agent) => agent.id === agentParam) ?? null;
  const names = systemDisplayNames(workspaces);
  const coverage = selectedAgent
    ? workspaces.map((workspace) => ({
        workspace,
        missing: missingSystems(selectedAgent.systems, workspace),
      }))
    : [];
  const selectedWorkspace =
    coverage.find((entry) => entry.workspace.id === workspaceParam && entry.missing.length === 0)
      ?.workspace ?? null;

  async function createAction(formData: FormData) {
    "use server";
    if (!selectedAgent || !selectedWorkspace) return;
    const name = String(formData.get("name") ?? "").trim();
    const { id } = await createRoutine({
      agentId: selectedAgent.id,
      workspaceId: selectedWorkspace.id,
      name,
    });
    redirect(`/app/routines/${id}`);
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header>
        <p className="text-xs text-muted">
          <Link href="/app/routines" className="underline">
            Routines
          </Link>
        </p>
        <h1 className="mt-1 font-display text-3xl text-ink">New routine</h1>
        <p className="mt-2 max-w-2xl text-sm text-muted">
          Pick the Agent that does the work, then a Workspace whose connectors cover what it
          uses. You will choose how it starts on the routine page.
        </p>
      </header>

      {configured.length === 0 ? (
        <EmptyState
          title="No configured Agents"
          body="Create an Agent with a goal before building a routine."
          action={
            <Link
              href="/app/agents"
              className="rounded-md border border-line bg-card px-3 py-1.5 text-sm font-medium text-ink hover:border-pine"
            >
              Create an Agent
            </Link>
          }
        />
      ) : (
        <div className="grid items-start gap-6 lg:grid-cols-2">
          <section aria-label="Pick an Agent">
            <h2 className="text-xs font-medium uppercase tracking-wide text-muted">
              1. Pick an Agent
            </h2>
            <ul className="m-0 mt-3 list-none space-y-2 p-0">
              {configured.map((agent) => {
                const active = agent.id === selectedAgent?.id;
                return (
                  <li key={agent.id}>
                    <Link
                      href={`/app/routines/new?agent=${agent.id}`}
                      aria-current={active ? "true" : undefined}
                      className={[
                        "block rounded-lg border bg-card p-4 hover:border-pine",
                        active ? "border-pine" : "border-line",
                      ].join(" ")}
                    >
                      <span className="block text-sm font-medium text-ink">{agent.name}</span>
                      <span className="mt-1 block text-sm text-muted">{agent.goal}</span>
                      <span className="mt-2 block font-mono text-xs uppercase text-muted">
                        {money(agent.budgetCapUsd)} per run
                      </span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </section>

          <section aria-label="Pick a Workspace" className="space-y-6">
            <div>
              <h2 className="text-xs font-medium uppercase tracking-wide text-muted">
                2. Pick a Workspace
              </h2>
              {!selectedAgent ? (
                <p className="mt-3 rounded-lg border border-line-soft bg-card px-6 py-8 text-center text-sm text-muted">
                  Pick an Agent first
                </p>
              ) : workspaces.length === 0 ? (
                <p className="mt-3 rounded-lg border border-line-soft bg-card px-6 py-8 text-center text-sm text-muted">
                  No workspaces yet. Create one with the connectors this Agent uses.
                </p>
              ) : (
                <ul className="m-0 mt-3 list-none space-y-2 p-0">
                  {coverage.map(({ workspace, missing }) => {
                    const active = workspace.id === selectedWorkspace?.id;
                    const covered = missing.length === 0;
                    const body = (
                      <>
                        <span className="block text-sm font-medium text-ink">{workspace.name}</span>
                        <span className="mt-1 block text-sm text-muted">
                          {workspace.systems.map((grant) => grant.displayName).join(", ") ||
                            "No connectors granted"}
                        </span>
                        {!covered && (
                          <span className="mt-2 block text-xs text-hold-text">
                            Missing {missing.map((id) => names[id] ?? id).join(", ")}
                          </span>
                        )}
                      </>
                    );
                    return (
                      <li key={workspace.id}>
                        {covered ? (
                          <Link
                            href={`/app/routines/new?agent=${selectedAgent.id}&workspace=${workspace.id}`}
                            aria-current={active ? "true" : undefined}
                            className={[
                              "block rounded-lg border bg-card p-4 hover:border-pine",
                              active ? "border-pine" : "border-line",
                            ].join(" ")}
                          >
                            {body}
                          </Link>
                        ) : (
                          <div className="rounded-lg border border-line-soft bg-card p-4 opacity-70">
                            {body}
                          </div>
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>

            {selectedAgent && selectedWorkspace && (
              <div className="rounded-lg border border-line bg-card p-5">
                <h2 className="text-xs font-medium uppercase tracking-wide text-muted">
                  3. Name it
                </h2>
                <form action={createAction} className="mt-3 space-y-3">
                  <input
                    name="name"
                    defaultValue={`${selectedAgent.name} in ${selectedWorkspace.name}`}
                    maxLength={120}
                    aria-label="Routine name"
                    className="w-full rounded-md border border-line bg-field px-2 py-1.5 text-sm text-ink"
                  />
                  <Button type="submit">Create routine</Button>
                  <p className="text-xs text-muted">
                    New routines start on demand; add a schedule or event trigger on the next
                    page.
                  </p>
                </form>
              </div>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
