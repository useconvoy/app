/**
 * billing_accounts access (DESIGN §3, §8): one row per org, plan and
 * status arriving only by Stripe webhook, read by the admin billing page.
 * Every query runs through withOrgContext so RLS bounds it; the webhook's
 * org id comes from Stripe-signed customer metadata written by Convoy
 * staff, which is the one caller whose org context does not derive from a
 * session (documented at the call site).
 */
import "server-only";

import { withOrgContext } from "@/lib/db";

export interface BillingAccount {
  orgId: string;
  stripeCustomerId: string | null;
  plan: string | null;
  status: string | null;
  updatedAt: Date;
}

export async function getBillingAccount(orgId: string): Promise<BillingAccount | null> {
  return withOrgContext({ orgId }, async (client) => {
    const { rows } = await client.query<BillingAccount>(
      `SELECT org_id AS "orgId", stripe_customer_id AS "stripeCustomerId",
              plan, status, updated_at AS "updatedAt"
         FROM billing_accounts WHERE org_id = $1`,
      [orgId],
    );
    return rows[0] ?? null;
  });
}

export interface BillingSyncPatch {
  stripeCustomerId: string;
  /** Absent fields keep their stored value (customer events carry no plan). */
  plan?: string | null;
  status?: string | null;
}

/**
 * Webhook upsert. Returns false when the org id does not name a known
 * organization, so unknown metadata is ignored rather than erroring; the
 * existence probe rides the org_select policy (id = app_org_id()).
 */
export async function syncBillingAccount(orgId: string, patch: BillingSyncPatch): Promise<boolean> {
  return withOrgContext({ orgId }, async (client) => {
    const { rowCount } = await client.query("SELECT 1 FROM organizations WHERE id = $1", [orgId]);
    if (rowCount === 0) return false;
    await client.query(
      `INSERT INTO billing_accounts (org_id, stripe_customer_id, plan, status, updated_at)
       VALUES ($1, $2, $3, $4, now())
       ON CONFLICT (org_id) DO UPDATE SET
         stripe_customer_id = EXCLUDED.stripe_customer_id,
         plan = COALESCE(EXCLUDED.plan, billing_accounts.plan),
         status = COALESCE(EXCLUDED.status, billing_accounts.status),
         updated_at = now()`,
      [orgId, patch.stripeCustomerId, patch.plan ?? null, patch.status ?? null],
    );
    return true;
  });
}
