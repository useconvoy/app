import type { Metadata } from "next";

import {
  environmentsClient,
  listSystemConnections,
} from "@/lib/api/environments";
import { createWorkspace, customSystemDefinitions } from "@/lib/workspaces/actions";
import { requireWorkspacesPage } from "@/lib/workspaces/gate";
import { systemCatalog } from "@/lib/workspaces/system-catalog";
import { CreateWorkspaceModal } from "./CreateWorkspaceModal";
import { WorkspaceCards } from "./WorkspaceCards";

export const metadata: Metadata = { title: "Workspaces" };
export const dynamic = "force-dynamic";

/**
 * Workspaces list: Admin/Operator only, server-checked. Cards
 * carry the connects/used-by facts; the create modal hangs off the header,
 * and a fresh organization sees an honest empty state under it.
 */
export default async function WorkspacesPage() {
  const { session } = await requireWorkspacesPage();
  const client = environmentsClient();
  const [workspaces, agents, connections, customSystems] = await Promise.all([
    client.listWorkspaces(session.orgId),
    client.listAgents(session.orgId),
    listSystemConnections(session.orgId),
    customSystemDefinitions(session.orgId),
  ]);
  const byProvider = new Map(
    (connections ?? []).map((connection) => [connection.provider, connection]),
  );
  const byConnectionId = new Map(
    (connections ?? []).map((connection) => [connection.connectionId, connection]),
  );
  const statusOf = (
    connection?: { status: string; hasCredential: boolean },
    credentialOptional = false,
  ) =>
    !connection
      ? ("not_connected" as const)
      : connection.status !== "active"
        ? ("needs_reauth" as const)
        : connection.hasCredential || credentialOptional
          ? ("connected" as const)
          : ("credentials_pending" as const);
  const cards = workspaces.map((workspace) => ({
    id: workspace.id,
    name: workspace.name,
    purpose: workspace.purpose,
    systemCount: workspace.systems.length,
    agentCount: agents.filter((agent) => agent.workspaceId === workspace.id).length,
  }));
  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl text-ink">Workspaces</h1>
          <p className="mt-2 text-sm text-muted">
            Connect shared systems and spaces once, then let multiple Agents operate through them.
          </p>
        </div>
        <CreateWorkspaceModal
          systems={[
            ...systemCatalog.map((system) => ({
              id: system.id,
              displayName: system.displayName,
              sideEffecting: system.sideEffecting,
              standInNote: system.standInNote,
              status: system.connection
                ? statusOf(byProvider.get(system.connection.provider))
                : ("no_provider" as const),
            })),
            ...customSystems.map((system) => ({
              id: system.id,
              displayName: system.displayName,
              sideEffecting: system.sideEffecting,
              standInNote: system.standInNote,
              status: statusOf(byConnectionId.get(system.connectionId), true),
            })),
          ]}
          create={createWorkspace}
        />
      </header>
      <WorkspaceCards workspaces={cards} />
    </div>
  );
}
