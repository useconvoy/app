/**
 * Org settings (migration 0004): the jsonb home for DESIGN §5's Admin
 * "policies & budget defaults" row and the org notification defaults.
 * This module is the only writer of organizations.settings; every shape
 * goes through zod on the way in, and reads tolerate missing or older
 * shapes by falling back to defaults. Admin mutations write admin_audit
 * rows in the same transaction (§9).
 */
import "server-only";

import type { PoolClient } from "pg";
import { z } from "zod";

import { withOrgContext, type DbContext } from "@/lib/db";
import { NOTIFICATION_CLASSES } from "@/notifier/rules";

export const policiesSchema = z.object({
  /** Default budget cap for a run, dollars (DESIGN §5 Admin row). */
  defaultRunBudgetCapUsd: z.number().positive().max(100_000),
  /** Monthly spend that triggers an admin notice, dollars. */
  monthlySpendNoticeUsd: z.number().positive().max(1_000_000),
  /** §9: Viewer's evidence-export right is org-policy toggleable. */
  viewerEvidenceExport: z.boolean(),
});

export type OrgPolicies = z.infer<typeof policiesSchema>;

/** Defaults mirror the demo world's budget shapes and an open export. */
export const DEFAULT_POLICIES: OrgPolicies = {
  defaultRunBudgetCapUsd: 75,
  monthlySpendNoticeUsd: 500,
  viewerEvidenceExport: true,
};

const channelSchema = z.enum(["in_app", "email", "slack"]);

/** Per-class default channels the notifier falls back to (v1: in_app). */
export const notificationDefaultsSchema = z.partialRecord(
  z.enum(NOTIFICATION_CLASSES),
  z.array(channelSchema).max(3),
);

export type NotificationDefaults = z.infer<typeof notificationDefaultsSchema>;

export interface OrgSettings {
  policies: OrgPolicies;
  notificationDefaults: NotificationDefaults;
}

async function readSettings(client: PoolClient, orgId: string): Promise<unknown> {
  const { rows } = await client.query<{ settings: unknown }>(
    "SELECT settings FROM organizations WHERE id = $1",
    [orgId],
  );
  return rows[0]?.settings ?? {};
}

function parseSettings(raw: unknown): OrgSettings {
  const record = (raw ?? {}) as Record<string, unknown>;
  const policies = policiesSchema.safeParse(record.policies);
  const defaults = notificationDefaultsSchema.safeParse(record.notification_defaults);
  return {
    policies: policies.success ? policies.data : DEFAULT_POLICIES,
    notificationDefaults: defaults.success ? defaults.data : {},
  };
}

/** The org's settings, defaults filled in for anything unset. */
export async function getOrgSettings(orgId: string): Promise<OrgSettings> {
  return withOrgContext({ orgId }, async (client) => parseSettings(await readSettings(client, orgId)));
}

async function auditRow(
  client: PoolClient,
  orgId: string,
  actorId: string,
  action: string,
  subject: string,
): Promise<void> {
  await client.query(
    "INSERT INTO admin_audit (org_id, actor_id, action, subject) VALUES ($1, $2, $3, $4)",
    [orgId, actorId, action, subject],
  );
}

/**
 * Persist policies under settings.policies with an audit row in the same
 * transaction. Called by the admin action after its gate; exercised
 * directly by the integration suite. Input must already be zod-clean.
 */
export async function savePoliciesCore(ctx: Required<DbContext>, policies: OrgPolicies): Promise<void> {
  const parsed = policiesSchema.parse(policies);
  await withOrgContext(ctx, async (client) => {
    await client.query(
      `UPDATE organizations
          SET settings = jsonb_set(settings, '{policies}', $2::jsonb)
        WHERE id = $1`,
      [ctx.orgId, JSON.stringify(parsed)],
    );
    await auditRow(
      client,
      ctx.orgId,
      ctx.userId,
      "org.policies_updated",
      `cap ${parsed.defaultRunBudgetCapUsd} notice ${parsed.monthlySpendNoticeUsd} viewer_export ${parsed.viewerEvidenceExport}`,
    );
  });
}

/** Persist notification defaults under settings.notification_defaults. */
export async function saveNotificationDefaultsCore(
  ctx: Required<DbContext>,
  defaults: NotificationDefaults,
): Promise<void> {
  const parsed = notificationDefaultsSchema.parse(defaults);
  await withOrgContext(ctx, async (client) => {
    await client.query(
      `UPDATE organizations
          SET settings = jsonb_set(settings, '{notification_defaults}', $2::jsonb)
        WHERE id = $1`,
      [ctx.orgId, JSON.stringify(parsed)],
    );
    await auditRow(
      client,
      ctx.orgId,
      ctx.userId,
      "org.notification_defaults_updated",
      Object.keys(parsed).join(", ") || "cleared",
    );
  });
}
