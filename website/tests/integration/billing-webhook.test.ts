// @vitest-environment node
/**
 * Stripe webhook -> billing_accounts sync (DESIGN §8, D11) against a real
 * Postgres. Events are signed with the SDK's own test-header helper and
 * POSTed straight to the route handler: a good signature syncs the org's
 * row, a bad signature is a 400 with no write, unknown org metadata and
 * unknown event types are acknowledged with 200 and ignored. The suite
 * skips cleanly when no database is provided.
 */
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import pg from "pg";
import Stripe from "stripe";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { POST } from "@/app/api/webhooks/stripe/route";
import { newTenantId } from "@/lib/orgs/validation";

const ADMIN_DSN = process.env.WEBSITE_PG_ADMIN_DSN;
const APP_DSN =
  process.env.WEBSITE_PG_DSN ??
  "postgresql://convoy_website_app:convoy_website_app@localhost:5440/convoy_website";
const WEBSITE_ROOT = new URL("../..", import.meta.url).pathname;

const WEBHOOK_SECRET = "whsec_test_billing_webhook_suite";
const run = randomUUID().slice(0, 8);
const orgId = randomUUID();
const customerId = `cus_test_${run}`;

// Signature helpers only; constructs no requests and needs no real key.
const signer = new Stripe("signature-verification-only").webhooks;

function eventPayload(type: string, object: Record<string, unknown>): string {
  return JSON.stringify({
    id: `evt_${randomUUID().replaceAll("-", "")}`,
    object: "event",
    api_version: "2026-06-30",
    created: Math.floor(Date.now() / 1000),
    type,
    data: { object },
    livemode: false,
    pending_webhooks: 0,
    request: { id: null, idempotency_key: null },
  });
}

function subscriptionObject(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: `sub_test_${run}`,
    object: "subscription",
    customer: customerId,
    status: "active",
    metadata: { org_id: orgId },
    items: { object: "list", data: [{ price: { id: "price_convoy_std", nickname: "Convoy standard" } }] },
    ...overrides,
  };
}

async function post(payload: string, signature?: string): Promise<Response> {
  const header =
    signature ?? signer.generateTestHeaderString({ payload, secret: WEBHOOK_SECRET });
  return POST(
    new Request("http://localhost/api/webhooks/stripe", {
      method: "POST",
      body: payload,
      headers: { "stripe-signature": header, "content-type": "application/json" },
    }),
  );
}

describe.skipIf(!ADMIN_DSN)("Stripe webhook -> billing_accounts (real Postgres)", () => {
  let admin: pg.Client;

  beforeAll(async () => {
    execFileSync(process.execPath, ["scripts/migrate.mjs"], {
      cwd: WEBSITE_ROOT,
      env: { ...process.env, WEBSITE_PG_ADMIN_DSN: ADMIN_DSN as string },
      stdio: "pipe",
    });
    process.env.WEBSITE_PG_DSN = APP_DSN;
    process.env.STRIPE_WEBHOOK_SECRET = WEBHOOK_SECRET;
    // No API client: org resolution must ride the signed metadata alone.
    delete process.env.STRIPE_SECRET_KEY;

    admin = new pg.Client({ connectionString: ADMIN_DSN });
    await admin.connect();
    await admin.query("INSERT INTO organizations (id, tenant_id, name) VALUES ($1, $2, $3)", [
      orgId,
      newTenantId(),
      `Billing Org ${run}`,
    ]);
  }, 30_000);

  afterAll(async () => {
    delete process.env.STRIPE_WEBHOOK_SECRET;
    await globalThis.__convoyWebsitePool?.end();
    globalThis.__convoyWebsitePool = undefined;
    if (!admin) return;
    await admin.query("DELETE FROM billing_accounts WHERE org_id = $1", [orgId]);
    await admin.query("DELETE FROM organizations WHERE id = $1", [orgId]);
    await admin.end();
  });

  async function billingRow(): Promise<
    { stripe_customer_id: string; plan: string | null; status: string | null } | undefined
  > {
    const { rows } = await admin.query(
      "SELECT stripe_customer_id, plan, status FROM billing_accounts WHERE org_id = $1",
      [orgId],
    );
    return rows[0];
  }

  it("a signed subscription update upserts plan and status for the metadata org", async () => {
    const response = await post(
      eventPayload("customer.subscription.updated", subscriptionObject()),
    );
    expect(response.status).toBe(200);
    expect(await billingRow()).toEqual({
      stripe_customer_id: customerId,
      plan: "Convoy standard",
      status: "active",
    });
  });

  it("a customer update refreshes the customer id and keeps plan and status", async () => {
    const response = await post(
      eventPayload("customer.updated", {
        id: customerId,
        object: "customer",
        metadata: { org_id: orgId },
      }),
    );
    expect(response.status).toBe(200);
    expect(await billingRow()).toEqual({
      stripe_customer_id: customerId,
      plan: "Convoy standard",
      status: "active",
    });
  });

  it("a subscription deletion lands the canceled status", async () => {
    const response = await post(
      eventPayload("customer.subscription.deleted", subscriptionObject({ status: "canceled" })),
    );
    expect(response.status).toBe(200);
    expect((await billingRow())?.status).toBe("canceled");
  });

  it("a bad signature is a 400 and writes nothing", async () => {
    await admin.query("DELETE FROM billing_accounts WHERE org_id = $1", [orgId]);
    const payload = eventPayload("customer.subscription.updated", subscriptionObject());
    const response = await post(payload, "t=1,v1=deadbeef");
    expect(response.status).toBe(400);
    expect(await billingRow()).toBeUndefined();
  });

  it("unknown org metadata is acknowledged with 200 and ignored", async () => {
    const stranger = randomUUID();
    const response = await post(
      eventPayload(
        "customer.subscription.updated",
        subscriptionObject({ metadata: { org_id: stranger } }),
      ),
    );
    expect(response.status).toBe(200);
    const { rowCount } = await admin.query("SELECT 1 FROM billing_accounts WHERE org_id = $1", [
      stranger,
    ]);
    expect(rowCount).toBe(0);
    expect(await billingRow()).toBeUndefined();
  });

  it("an unhandled event type is acknowledged with 200 and ignored", async () => {
    const response = await post(
      eventPayload("invoice.finalized", { id: `in_${run}`, object: "invoice" }),
    );
    expect(response.status).toBe(200);
    expect(await billingRow()).toBeUndefined();
  });
});
