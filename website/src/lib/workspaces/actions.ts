/**
 * Server actions for workspace management, an Admin/Operator-only
 * surface. Org and actor derive from the verified session; the
 * permissions matrix is re-checked server-side; workspace management is
 * administrative, so every mutation writes an admin_audit row with actor
 * attribution. The workspace itself goes through the environments adapter
 * (lib/api/environments): the real registry when the E0 flag is set, the
 * org-scoped workspaces table otherwise.
 */
"use server";

import { revalidatePath } from "next/cache";

import {
  environmentsClient,
  listSystemConnections,
  type CustomWorkspaceSystem,
} from "@/lib/api/environments";
import { requireOrgSession } from "@/lib/auth/session";
import { withOrgContext } from "@/lib/db";
import { unconnectableGrants } from "@/lib/workspaces/connector-health";
import { catalogSystem } from "@/lib/workspaces/system-catalog";
import { getMembership } from "@/lib/orgs/queries";
import { can } from "@/lib/permissions";

/** The org's custom systems as grantable definitions, from the registry. */
export async function customSystemDefinitions(orgId: string): Promise<CustomWorkspaceSystem[]> {
  const connections = (await listSystemConnections(orgId)) ?? [];
  return connections
    .filter((connection) => connection.kind === "mcp_custom")
    .map((connection) => ({
      id: `custom:${connection.connectionId}`,
      connectionId: connection.connectionId,
      displayName: connection.displayName,
      // A system is side-effecting when any of its tools is (an empty
      // surface stays conservative until the server declares one).
      sideEffecting:
        connection.tools.length === 0 || connection.tools.some((tool) => tool.sideEffecting),
      standInNote: "Recorded intents only; nothing reaches the live server.",
      tools: connection.tools,
    }));
}

export interface CreateWorkspacePayload {
  name: string;
  purpose: string;
  systems: Array<{
    systemId: string;
    scope: "read" | "write";
    useStandIn: boolean;
    /** Actions enabled for this connector; absent keeps the full scope surface. */
    tools?: string[];
  }>;
}

export async function createWorkspace(payload: CreateWorkspacePayload): Promise<{ id: string }> {
  const session = await requireOrgSession();
  const membership = await getMembership(session.orgId, session.userId);
  if (
    !membership ||
    membership.status !== "active" ||
    !can("manage_workspaces", membership.role, membership.capabilities)
  ) {
    throw new Error("You cannot manage workspaces");
  }

  const name = payload.name.trim();
  if (name.length < 2 || name.length > 120) {
    throw new Error("Workspace name must be between 2 and 120 characters");
  }
  const purpose = payload.purpose.trim();
  if (purpose.length < 2 || purpose.length > 200) {
    throw new Error("Say which shared work this workspace supports");
  }
  if (payload.systems.length === 0) {
    throw new Error("Pick at least one connector");
  }
  // Custom connector grants ("custom:<connection>") resolve against the
  // registry connections, server-side; the modal list is a lens only.
  // One listing serves both resolution and the health check below.
  const connections = await listSystemConnections(session.orgId);
  const wantsCustom = payload.systems.some((choice) => choice.systemId.startsWith("custom:"));
  const customSystems = wantsCustom ? await customSystemDefinitions(session.orgId) : [];
  const customById = new Map(customSystems.map((definition) => [definition.id, definition]));

  for (const choice of payload.systems) {
    const system = catalogSystem(choice.systemId) ?? customById.get(choice.systemId);
    if (!system) throw new Error("Unknown connector");
    if (choice.scope !== "read" && choice.scope !== "write") {
      throw new Error("Unknown grant");
    }
    // An explicit action selection must name actions the connector
    // declares, and cannot be empty (deselect the connector instead).
    if (choice.tools) {
      const declared = new Set(
        ("connection" in system ? system.connection?.tools : system.tools)?.map(
          (tool) => tool.name,
        ) ?? [],
      );
      if (choice.tools.length === 0) {
        throw new Error(`Enable at least one action for ${system.displayName}`);
      }
      for (const tool of choice.tools) {
        if (!declared.has(tool)) {
          throw new Error(`${system.displayName} has no action named ${tool}`);
        }
      }
    }
  }

  // A workspace bundles healthy connectors. When the registry is linked,
  // every provider-backed or custom grant must resolve to an active
  // connection (with a verified credential, custom servers excepted)
  // before anything is created. The dev fallback has no connections to
  // check, so it keeps the old behavior.
  if (connections !== null) {
    const blocked = unconnectableGrants(payload.systems, connections);
    if (blocked.length > 0) {
      throw new Error(
        `Connect ${blocked.join(", ")} on the Connectors page before adding ${
          blocked.length === 1 ? "it" : "them"
        } to a workspace`,
      );
    }
  }

  const workspace = await environmentsClient().createWorkspace(session.orgId, {
    name,
    purpose,
    systems: payload.systems,
    customSystems,
  });
  await withOrgContext({ orgId: session.orgId, userId: session.userId }, (client) =>
    client
      .query("INSERT INTO admin_audit (org_id, actor_id, action, subject) VALUES ($1, $2, $3, $4)", [
        session.orgId,
        session.userId,
        "workspace.created",
        workspace.id,
      ])
      .then(() => undefined),
  );
  revalidatePath("/app/workspaces");
  return { id: workspace.id };
}
