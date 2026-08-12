/**
 * Connector health for workspace creation: a workspace bundles the
 * organization's connectors, so every grant must be backed by a healthy
 * connection before the workspace exists. Healthy means the registry says
 * the connection is active and it holds a verified credential; custom
 * connectors (org-run tool servers) may be intentionally public, so a
 * credential is optional for them. Pure functions so the rule is testable
 * apart from the session and the registry.
 */

import { catalogSystem } from "@/lib/workspaces/system-catalog";

export interface ConnectionFacts {
  connectionId: string;
  kind: string;
  provider: string;
  displayName: string;
  status: string;
  hasCredential: boolean;
}

export function isHealthyConnection(
  connection: ConnectionFacts | undefined,
  credentialOptional = false,
): boolean {
  if (!connection) return false;
  if (connection.status !== "active") return false;
  return connection.hasCredential || credentialOptional;
}

/**
 * The display names of granted connectors that are NOT backed by a healthy
 * connection, in grant order. Catalog systems without a registry provider
 * pass (there is no external connection to be unhealthy); custom grants
 * ("custom:<connectionId>") resolve by connection id with the credential
 * optional.
 */
export function unconnectableGrants(
  choices: Array<{ systemId: string }>,
  connections: ConnectionFacts[],
): string[] {
  const byProvider = new Map(connections.map((connection) => [connection.provider, connection]));
  const byId = new Map(connections.map((connection) => [connection.connectionId, connection]));
  const blocked: string[] = [];
  for (const choice of choices) {
    if (choice.systemId.startsWith("custom:")) {
      const connection = byId.get(choice.systemId.slice("custom:".length));
      if (!isHealthyConnection(connection, true)) {
        blocked.push(connection?.displayName ?? "a custom connector");
      }
      continue;
    }
    const spec = catalogSystem(choice.systemId);
    if (!spec?.connection) continue;
    if (!isHealthyConnection(byProvider.get(spec.connection.provider))) {
      blocked.push(spec.displayName);
    }
  }
  return blocked;
}
