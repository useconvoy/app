import type { Metadata } from "next";

import { environmentsClient } from "@/lib/api/environments";
import { createAgent } from "@/lib/agents/actions";
import { requireWorkspacesPage } from "@/lib/workspaces/gate";
import { AgentCards } from "./AgentCards";
import { CreateAgentModal } from "./CreateAgentModal";

export const metadata: Metadata = { title: "Agents" };
export const dynamic = "force-dynamic";

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
        <CreateAgentModal
          workspaces={workspaces.map((workspace) => ({
            id: workspace.id,
            name: workspace.name,
            systemCount: workspace.systems.length,
          }))}
          create={createAgent}
        />
      </header>
      {workspaces.length === 0 && (
        <p role="status" className="rounded-md border border-hold-soft bg-hold-soft p-3 text-sm text-hold-text">
          Create a workspace and connect its systems before creating an agent.
        </p>
      )}
      <AgentCards agents={agents} workspaceNames={workspaceNames} />
    </div>
  );
}
