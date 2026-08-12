/**
 * Server actions for the Connectors page: connecting a provider (Slack,
 * Google Drive) by pasting its credential, registering a custom connector
 * (a tool server the org runs), and re-attaching credentials.
 * Admin/Operator-only, same matrix as workspace management. Credential
 * values pass straight through to the registry (write-only there) and are
 * never stored or logged website-side; the admin_audit row records the
 * act, not the material.
 */
"use server";

import { revalidatePath } from "next/cache";

import {
  attachSystemCredential,
  connectCustomSystem,
  connectManagedSystem,
  verifySystemConnection,
} from "@/lib/api/environments";
import { declaredManifest } from "@/lib/api/environments-mapping";
import { requireOrgSession } from "@/lib/auth/session";
import { withOrgContext } from "@/lib/db";
import { getMembership } from "@/lib/orgs/queries";
import { can } from "@/lib/permissions";
import { catalogSystem } from "@/lib/workspaces/system-catalog";

export interface ConnectorActionResult {
  ok: boolean;
  message: string;
}

async function requireConnectorsActor() {
  const session = await requireOrgSession();
  const membership = await getMembership(session.orgId, session.userId);
  if (
    !membership ||
    membership.status !== "active" ||
    !can("manage_workspaces", membership.role, membership.capabilities)
  ) {
    throw new Error("You cannot manage connectors");
  }
  return session;
}

async function audit(orgId: string, userId: string, action: string, subject: string): Promise<void> {
  await withOrgContext({ orgId, userId }, async (client) => {
    await client.query(
      "INSERT INTO admin_audit (org_id, actor_id, action, subject) VALUES ($1, $2, $3, $4)",
      [orgId, userId, action, subject],
    );
  });
}

/** The registry's "stored but failed verification" reply, made plain. */
function verificationMessage(error: unknown): string {
  const text = error instanceof Error ? error.message : String(error);
  if (text.includes("verification failed")) {
    return "The credential was saved, but checking it with the provider failed. Fix the value and try again.";
  }
  return "That did not work. Check the details and try again.";
}

/** Connect a provider by pasting its credential. */
export async function connectProvider(
  systemId: string,
  secretValue: string,
): Promise<ConnectorActionResult> {
  const session = await requireConnectorsActor();
  const system = catalogSystem(systemId);
  if (!system?.connection) throw new Error("No provider serves that connector yet");
  if (secretValue.trim().length === 0) {
    return { ok: false, message: "Paste the credential first." };
  }
  try {
    const result = await connectManagedSystem(session.orgId, {
      provider: system.connection.provider,
      displayName: system.displayName,
      manifest: declaredManifest(system.connection),
      secretValue: secretValue.trim(),
    });
    await audit(session.orgId, session.userId, "connector.connected", `${systemId} (${result.status})`);
    revalidatePath("/app/connectors");
    revalidatePath("/app/workspaces");
    return {
      ok: result.status === "active",
      message:
        result.status === "active"
          ? `${system.displayName} is connected.`
          : "Saved, but the provider did not accept the credential yet.",
    };
  } catch (error) {
    await audit(session.orgId, session.userId, "connector.connect_failed", systemId);
    revalidatePath("/app/connectors");
    return { ok: false, message: verificationMessage(error) };
  }
}

/** Register a custom connector: a remote MCP server this org operates. */
export async function addCustomConnector(
  displayName: string,
  url: string,
  bearerToken: string,
): Promise<ConnectorActionResult> {
  const session = await requireConnectorsActor();
  const name = displayName.trim();
  const target = url.trim();
  if (name.length < 2) return { ok: false, message: "Give the connector a name." };
  let parsed: URL;
  try {
    parsed = new URL(target);
  } catch {
    return { ok: false, message: "That is not a full server address." };
  }
  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
    return { ok: false, message: "The server address must start with https." };
  }
  try {
    const created = await connectCustomSystem(session.orgId, {
      displayName: name,
      url: target,
      bearerToken: bearerToken.trim() || undefined,
    });
    await audit(session.orgId, session.userId, "connector.custom_added", `${name} (${created.tools.length} tools)`);
    revalidatePath("/app/connectors");
    revalidatePath("/app/workspaces");
    return {
      ok: true,
      message: `${name} answered with ${created.tools.length} ${
        created.tools.length === 1 ? "tool" : "tools"
      }: ${created.tools.join(", ")}`,
    };
  } catch (error) {
    return { ok: false, message: verificationMessage(error) };
  }
}

/** Re-check a connection's health against its provider, on demand. */
export async function checkConnectorHealth(
  connectionId: string,
): Promise<ConnectorActionResult> {
  const session = await requireConnectorsActor();
  try {
    const result = await verifySystemConnection(session.orgId, connectionId);
    await audit(session.orgId, session.userId, "connector.health_checked",
      `${connectionId} (${result.status})`);
    revalidatePath("/app/connectors");
    if (result.verified === true) {
      return { ok: true, message: "Healthy. The provider accepted the credential." };
    }
    if (result.verified === false) {
      return { ok: false, message: "The provider rejected the credential. Reconnect it below." };
    }
    return {
      ok: true,
      message:
        result.reason === "no credential stored"
          ? "No credential is stored yet. Connect it first."
          : "This provider has no automatic check; its first run will tell.",
    };
  } catch {
    return { ok: false, message: "The health check could not run. Try again in a moment." };
  }
}

/** Replace the credential on an existing connection. */
export async function reattachCredential(
  connectionId: string,
  secretValue: string,
): Promise<ConnectorActionResult> {
  const session = await requireConnectorsActor();
  if (secretValue.trim().length === 0) {
    return { ok: false, message: "Paste the credential first." };
  }
  try {
    const result = await attachSystemCredential(session.orgId, connectionId, secretValue.trim());
    await audit(session.orgId, session.userId, "connector.credential_replaced",
      `${connectionId} (${result.status})`);
    revalidatePath("/app/connectors");
    return { ok: result.status === "active", message: "Credential updated." };
  } catch (error) {
    await audit(session.orgId, session.userId, "connector.credential_rejected", connectionId);
    revalidatePath("/app/connectors");
    return { ok: false, message: verificationMessage(error) };
  }
}
