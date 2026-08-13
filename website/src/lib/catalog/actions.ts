/**
 * Server actions for the catalog. Org and actor derive from
 * the verified session, the permissions matrix is re-checked server-side,
 * and administrative writes land admin_audit rows. Publishing is
 * platform-side only for now (the deploy seeds Convoy's entries through
 * scripts/seed-doc-steward.mjs and its successors); a self-serve publish
 * surface returns later on top of publishEntryCore.
 */
"use server";

import { revalidatePath } from "next/cache";

import { environmentsClient } from "@/lib/api/environments";
import { requireOrgSession } from "@/lib/auth/session";
import { withOrgContext } from "@/lib/db";
import { getMembership } from "@/lib/orgs/queries";
import { getOrgSettings } from "@/lib/orgs/settings";
import { can, type Action } from "@/lib/permissions";
import { upsertInstalledRoutine } from "@/lib/routines/queries";
import { computeCompatibility, reportState } from "./compat";
import { getEntry } from "./queries";

/** Session + matrix gate shared by the catalog actions. */
async function requireCatalogAction(action: Action) {
  const session = await requireOrgSession();
  const membership = await getMembership(session.orgId, session.userId);
  if (
    !membership ||
    membership.status !== "active" ||
    !can(action, membership.role, membership.capabilities)
  ) {
    throw new Error("You cannot do that in this organization");
  }
  return { session, membership };
}

/**
 * The install flow's completion: recompute the compatibility report
 * server-side (the client rendering is convenience, not enforcement) and
 * refuse while systems are missing. Installing is the only way an Agent
 * comes to exist: a first install creates the Agent in the chosen
 * workspace from the template's goal and requirements; reinstalling an
 * entry re-pins the Agent that already carries it. The Agent's budget
 * starts at the org's default per-run cap; the admin_audit row attributes
 * the install in the same transaction as the pin.
 */
export async function installEntry(
  entryId: string,
  workspaceId: string,
): Promise<{ pinnedVersion: number; agentId: string }> {
  const { session } = await requireCatalogAction("manage_workspaces");
  const entry = await getEntry({ orgId: session.orgId, userId: session.userId }, entryId);
  if (!entry) throw new Error("That Agent template is not on the storefront");
  const workspace = await environmentsClient().getWorkspace(session.orgId, workspaceId);
  if (!workspace) throw new Error("That workspace does not exist");

  const report = computeCompatibility(entry.requirements, workspace);
  if (reportState(report) === "needs_connect") {
    throw new Error("Connect the missing systems before installing");
  }

  const settings = await getOrgSettings(session.orgId);
  const ctx = { orgId: session.orgId, userId: session.userId };
  const alreadyInstalled = await withOrgContext(ctx, async (client) => {
    const { rows } = await client.query<{ id: string }>(
      "SELECT id FROM agents WHERE org_id = $1 AND source_entry_id = $2",
      [session.orgId, entry.id],
    );
    return rows[0]?.id ?? null;
  });
  const agentId =
    alreadyInstalled ??
    (
      await environmentsClient().createAgent(session.orgId, {
        workspaceId: workspace.id,
        name: entry.storefront.name,
        purpose: entry.storefront.tagline,
        goal: entry.storefront.description,
        planSteps: [],
        scheduleDescription: null,
        budgetCapUsd: settings.policies.defaultRunBudgetCapUsd,
        sandboxTemplate: "convoy-devbox-python",
        browserPolicy: null,
        makeDefault: false,
      })
    ).id;

  const installedAgentId = await withOrgContext(ctx, async (client) => {
    const installed = await upsertInstalledRoutine(client, ctx, {
      name: entry.storefront.name,
      descriptor: entry.storefront.tagline,
      // The description is the working goal the runtime plans from; the
      // tagline stays the one-line storefront descriptor.
      goal: entry.storefront.description,
      systems: entry.requirements.systems.map((required) => required.systemId),
      budgetCapUsd: settings.policies.defaultRunBudgetCapUsd,
      workspaceId: workspace.id,
      agentId,
      sourceEntryId: entry.id,
      sourceVersion: entry.version,
    });
    await client.query(
      "INSERT INTO admin_audit (org_id, actor_id, action, subject) VALUES ($1, $2, $3, $4)",
      [
        session.orgId,
        session.userId,
        "catalog.entry_installed",
        `${entry.routineId}@v${entry.version} installed on ${installed.id}`,
      ],
    );
    return installed.id;
  });
  revalidatePath("/app/catalog");
  revalidatePath("/app/agents");
  revalidatePath("/app/routines");
  revalidatePath(`/app/agents/${installedAgentId}`);
  return { pinnedVersion: entry.version, agentId: installedAgentId };
}
