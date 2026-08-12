import type { Metadata } from "next";

import {
  listProviderDirectory,
  listSystemConnections,
  type SystemConnection,
} from "@/lib/api/environments";
import {
  addCustomConnector,
  checkConnectorHealth,
  connectProvider,
  reattachCredential,
} from "@/lib/connectors/actions";
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
  const [connections, directory] = await Promise.all([
    listSystemConnections(session.orgId),
    listProviderDirectory(session.orgId),
  ]);

  const byProvider = new Map((connections ?? []).map((connection) => [connection.provider, connection]));
  const catalogIdByProvider = new Map(
    systemCatalog
      .filter((system) => system.connection !== null)
      .map((system) => [system.connection!.provider, system]),
  );

  // The registry-served directory is authoritative when linked: a provider
  // added service-side appears here with no website change. Code providers
  // keep their catalog grant ids; declarative platform providers connect
  // through the custom-connection mechanism and are matched back to their
  // directory entry by display name. The static catalog remains the
  // fallback for unlinked (dev) deployments.
  let connectors: ConnectorCard[];
  let customSource: SystemConnection[];
  if (directory !== null) {
    const declarativeNames = new Set(
      directory.filter((entry) => entry.kind === "declarative").map((entry) => entry.displayName),
    );
    connectors = directory.map((entry) => {
      const catalogEntry = catalogIdByProvider.get(entry.provider);
      const connection =
        entry.kind === "code"
          ? byProvider.get(entry.provider)
          : (connections ?? []).find(
              (candidate) =>
                candidate.kind === "mcp_custom" && candidate.displayName === entry.displayName,
            );
      return {
        systemId: catalogEntry?.id ?? `platform:${entry.provider}`,
        displayName: entry.displayName,
        provider: entry.provider,
        connectionId: connection?.connectionId ?? null,
        state: connectionState(connection),
        toolCount: connection?.toolCount ?? entry.tools.length,
        sideEffecting: entry.tools.some((tool) => tool.sideEffecting),
        blurb: entry.description || undefined,
        credential: entry.credential,
      };
    });
    // Declarative connections render as directory cards, not custom rows.
    customSource = (connections ?? []).filter(
      (connection) =>
        connection.kind === "mcp_custom" && !declarativeNames.has(connection.displayName),
    );
  } else {
    connectors = systemCatalog
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
    customSource = (connections ?? []).filter((connection) => connection.kind === "mcp_custom");
  }
  const customConnectors = customSource.map((connection) => ({
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
        checkHealth={checkConnectorHealth}
      />
    </div>
  );
}
