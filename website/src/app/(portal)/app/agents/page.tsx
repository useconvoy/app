import type { Metadata } from "next";
import Link from "next/link";

import { environmentsClient } from "@/lib/api/environments";
import { requireWorkspacesPage } from "@/lib/workspaces/gate";
import { AgentCards } from "./AgentCards";

export const metadata: Metadata = { title: "Agents" };
export const dynamic = "force-dynamic";

/**
 * The Agents list. Agents are not authored by hand: they arrive by
 * installing a template from the catalog, which creates the Agent in a
 * chosen Workspace along with its first routine.
 */
export default async function AgentsPage() {
  const { session } = await requireWorkspacesPage();
  const client = environmentsClient();
  const [workspaces, agents] = await Promise.all([
    client.listWorkspaces(session.orgId),
    client.listAgents(session.orgId),
  ]);
  const workspaceNames = Object.fromEntries(
    workspaces.map((workspace) => [workspace.id, workspace.name]),
  );
  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl text-ink">Agents</h1>
          <p className="mt-2 max-w-2xl text-sm text-muted">
            Each Agent has its own goal and runtime setup, and operates through a shared Workspace. Multiple Agents can use the same Workspace and its integrations.
          </p>
        </div>
        <Link
          href="/app/catalog"
          className="rounded-md border border-line bg-card px-3 py-1.5 text-sm font-medium text-ink hover:border-pine"
        >
          Install from the catalog
        </Link>
      </header>
      {workspaces.length === 0 && (
        <p role="status" className="rounded-md border border-hold-soft bg-hold-soft p-3 text-sm text-hold-text">
          Create a workspace and connect its systems; installing an agent needs a workspace to
          run in.
        </p>
      )}
      <AgentCards agents={agents} workspaceNames={workspaceNames} />
    </div>
  );
}
