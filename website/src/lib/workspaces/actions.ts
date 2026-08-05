/**
 * Server actions for workspace management (Admin/Operator surface,
 * DESIGN §2). Org and actor derive from the verified session; the
 * permissions matrix is re-checked server-side; workspace management is
 * administrative, so every mutation writes an admin_audit row with actor
 * attribution. The workspace itself lands in the environments fixture
 * store until the registry exists. TODO(environments-E0).
 */
"use server";

import { revalidatePath } from "next/cache";

import { environmentsClient } from "@/lib/api/environments";
import { requireOrgSession } from "@/lib/auth/session";
import { withOrgContext } from "@/lib/db";
import { catalogSystem } from "@/lib/fixtures/environments";
import { copy } from "@/lexicon";
import { getMembership } from "@/lib/orgs/queries";
import { can } from "@/lib/permissions";

export interface CreateWorkspacePayload {
  name: string;
  purpose: string;
  systems: Array<{ systemId: string; scope: "read" | "write"; useStandIn: boolean }>;
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
    throw new Error("Say in a sentence what runs here");
  }
  if (payload.systems.length === 0) {
    throw new Error("Connect at least one system");
  }
  for (const choice of payload.systems) {
    const system = catalogSystem(choice.systemId);
    if (!system) throw new Error("Unknown system");
    if (choice.scope !== "read" && choice.scope !== "write") {
      throw new Error("Unknown grant");
    }
    // The modal blocks this before submit; the server holds the same line.
    if (system.sideEffecting && choice.scope === "write" && !choice.useStandIn) {
      throw new Error(copy.standInRequired(system.displayName));
    }
  }

  const workspace = await environmentsClient().createWorkspace(session.orgId, {
    name,
    purpose,
    systems: payload.systems,
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
