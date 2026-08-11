import type { Metadata } from "next";

import { listSystemConnections, type SystemConnection } from "@/lib/api/environments";
import { addCustomSystem, connectSystem, reattachCredential } from "@/lib/systems/actions";
import { requireWorkspacesPage } from "@/lib/workspaces/gate";
import { systemCatalog } from "@/lib/workspaces/system-catalog";
import { SystemsBoard, type ConnectionState, type SystemCard } from "./SystemsBoard";

export const metadata: Metadata = { title: "Systems" };
export const dynamic = "force-dynamic";

function connectionState(connection: SystemConnection | undefined): ConnectionState {
  if (!connection) return "not_connected";
  if (connection.status === "needs_reauth" || connection.status === "revoked") return "needs_reauth";
  // A custom tool server may intentionally be public. Registration already
  // called tools/list successfully, so no bearer token is not a pending state.
  if (!connection.hasCredential && connection.kind !== "mcp_custom") return "credentials_pending";
  return "connected";
}

/**
 * Systems: what this organization can reach and how it authorizes each
 * provider. Admin/Operator-only (same gate as workspaces — connecting a
 * system decides what workspaces may grant). The board reads live
 * connection state from the registry; without the registry link it says so
 * instead of pretending.
 */
export default async function SystemsPage() {
  const { session } = await requireWorkspacesPage();
  const connections = await listSystemConnections(session.orgId);

  const byProvider = new Map((connections ?? []).map((connection) => [connection.provider, connection]));
  const systems: SystemCard[] = systemCatalog.map((system) => {
    const connection = system.connection ? byProvider.get(system.connection.provider) : undefined;
    return {
      systemId: system.id,
      displayName: system.displayName,
      provider: system.connection?.provider ?? null,
      connectionId: connection?.connectionId ?? null,
      state: system.connection ? connectionState(connection) : "no_provider",
      toolCount: connection?.toolCount ?? system.connection?.tools.length ?? 0,
      sideEffecting: system.sideEffecting,
    };
  });
  const customSystems = (connections ?? [])
    .filter((connection) => connection.kind === "mcp_custom")
    .map((connection) => ({
      connectionId: connection.connectionId,
      displayName: connection.displayName,
      state: connectionState(connection),
      toolCount: connection.toolCount,
      tools: connection.tools.map((tool) => tool.name),
    }));

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <header>
        <h1 className="font-display text-3xl text-ink">Systems</h1>
        <p className="mt-2 text-sm text-muted">
          What this organization can reach, and how each provider authorizes it. Workspaces grant
          these to Agents. More than one Agent can use the same Workspace and its shared systems.
        </p>
      </header>
      <SystemsBoard
        registryLinked={connections !== null}
        systems={systems}
        customSystems={customSystems}
        connect={connectSystem}
        addCustom={addCustomSystem}
        reattach={reattachCredential}
      />
    </div>
  );
}
