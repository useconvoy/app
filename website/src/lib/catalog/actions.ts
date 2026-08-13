/**
 * Server actions for the catalog. Org and actor derive from
 * the verified session, the permissions matrix is re-checked server-side,
 * and administrative writes land admin_audit rows. Publishing snapshots
 * into catalog_entries; installing enriches one Agent with the pinned template.
 */
"use server";

import { revalidatePath } from "next/cache";
import { z } from "zod";

import { environmentsClient } from "@/lib/api/environments";
import { requireOrgSession } from "@/lib/auth/session";
import { withOrgContext } from "@/lib/db";
import { getMembership } from "@/lib/orgs/queries";
import { getOrgSettings } from "@/lib/orgs/settings";
import { can, type Action } from "@/lib/permissions";
import { upsertInstalledRoutine } from "@/lib/routines/queries";
import { computeCompatibility, reportState } from "./compat";
import { getEntry, publishEntryCore } from "./queries";

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

const publishSchema = z.object({
  routineId: z.string().min(1).max(200),
  version: z.number().int().positive().max(100_000),
  storefront: z.object({
    name: z.string().trim().min(2).max(120),
    tagline: z.string().trim().min(2).max(160),
    description: z.string().trim().min(2).max(2000),
  }),
  capabilityRequirements: z.object({
    systems: z
      .array(z.object({ systemId: z.string().min(1).max(120), scope: z.enum(["read", "write"]) }))
      .max(50),
    vendorSpecificTools: z
      .array(z.object({ tool: z.string().min(1).max(120), vendorSystemId: z.string().min(1).max(120) }))
      .max(200),
  }),
  evalThresholds: z.object({ minScore: z.number().min(0).max(100) }),
  note: z.string().trim().max(500).optional(),
});

export type PublishEntryInput = z.infer<typeof publishSchema>;

/**
 * Workshop-org publish: snapshot the routine version onto the storefront
 * with convoy visibility, appending to the entry's changelog.
 */
export async function publishEntry(input: PublishEntryInput): Promise<{ id: string; version: number }> {
  const { session } = await requireCatalogAction("publish_catalog");
  const parsed = publishSchema.safeParse(input);
  if (!parsed.success) {
    throw new Error("Check the publish form: every field needs a sensible value");
  }
  const result = await publishEntryCore(
    { orgId: session.orgId, userId: session.userId },
    parsed.data,
  );
  revalidatePath("/app/catalog");
  return result;
}

/**
 * The install flow's completion: recompute the compatibility report
 * server-side (the client rendering is convenience, not enforcement),
 * refuse while systems are missing, then enrich the selected Agent with the
 * pinned goal, system requirements, and budget. The Agent's budget starts at
 * the org's default per-run cap; the
 * admin_audit row attributes the install in the same transaction.
 */
export async function installEntry(
  entryId: string,
  agentId: string,
): Promise<{ pinnedVersion: number; agentId: string }> {
  const { session } = await requireCatalogAction("manage_workspaces");
  const entry = await getEntry({ orgId: session.orgId, userId: session.userId }, entryId);
  if (!entry) throw new Error("That Agent template is not on the storefront");
  const agent = await environmentsClient().getAgent(session.orgId, agentId);
  if (!agent) throw new Error("That agent does not exist");
  const workspace = await environmentsClient().getWorkspace(session.orgId, agent.workspaceId);
  if (!workspace) throw new Error("That workspace does not exist");

  const report = computeCompatibility(entry.requirements, workspace);
  if (reportState(report) === "needs_connect") {
    throw new Error("Connect the missing systems before installing");
  }

  const settings = await getOrgSettings(session.orgId);
  const ctx = { orgId: session.orgId, userId: session.userId };
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
      agentId: agent.id,
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
  revalidatePath(`/app/agents/${installedAgentId}`);
  return { pinnedVersion: entry.version, agentId: installedAgentId };
}
