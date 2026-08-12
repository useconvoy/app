import type { Metadata } from "next";

import { listSystemConnections, type SystemConnection } from "@/lib/api/environments";
import { addCustomConnector, connectProvider, reattachCredential } from "@/lib/connectors/actions";
import { requireWorkspacesPage } from "@/lib/workspaces/gate";
import { systemCatalog } from "@/lib/workspaces/system-catalog";
import { ConnectorsBoard, type ConnectionState, type ConnectorCard } from "./ConnectorsBoard";

export const metadata: Metadata = { title: "Connectors" };
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
 * Connectors: the services this organization connects and how each provider
 * authorizes it. Admin/Operator-only (same gate as workspaces; connecting a
 * service decides what workspaces may grant). The board reads live
 * connection state from the registry; without the registry link it says so
 * instead of pretending. Only catalog entries a provider actually serves
 * appear here; grants without a provider stay a workspace concern.
 */
export default async function ConnectorsPage() {
  const { session } = await requireWorkspacesPage();
  const connections = await listSystemConnections(session.orgId);

  const byProvider = new Map((connections ?? []).map((connection) => [connection.provider, connection]));
  const connectors: ConnectorCard[] = systemCatalog
    .filter((system) => system.connection !== null)
    .map((system) => {
      const connection = system.connection ? byProvider.get(system.connection.provider) : undefined;
      return {
        systemId: system.id,
        displayName: system.displayName,
        provider: system.connection?.provider ?? null,
        connectionId: connection?.connectionId ?? null,
        state: connectionState(connection),
        toolCount: connection?.toolCount ?? system.connection?.tools.length ?? 0,
        sideEffecting: system.sideEffecting,
      };
    });
  const customConnectors = (connections ?? [])
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
        <h1 className="font-display text-3xl text-ink">Connectors</h1>
        <p className="mt-2 text-sm text-muted">
          Connect the services this organization works through. Workspaces grant these
          connections to Agents, and more than one Agent can use the same Workspace and its
          shared connections.
        </p>
      </header>
      <ConnectorsBoard
        registryLinked={connections !== null}
        connectors={connectors}
        customConnectors={customConnectors}
        connect={connectProvider}
        addCustom={addCustomConnector}
        reattach={reattachCredential}
      />
    </div>
  );
}
