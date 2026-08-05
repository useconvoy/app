/**
 * POST /api/webhooks/stripe: the one writer of billing_accounts
 * (DESIGN §8, invoice-first D11). Plan and status arrive here by webhook;
 * the site itself never mutates them.
 *
 * Trust model: the signature over the raw body (STRIPE_WEBHOOK_SECRET)
 * authenticates the event; the org comes from metadata.org_id, which
 * Convoy staff set on the Stripe customer when provisioning (and copy
 * onto subscriptions, so subscription events resolve without an API
 * round-trip). That signed, staff-written metadata is the one org context
 * not derived from a session. Unknown events and unknown orgs are
 * acknowledged with 200 and ignored, so Stripe never retries them; a bad
 * signature is a 400. Nothing about the request is logged (iron rule 10:
 * no secrets in logs; the signature header and body stay out of them).
 */
import type Stripe from "stripe";
import { NextResponse } from "next/server";

import { syncBillingAccount, type BillingSyncPatch } from "@/lib/billing/queries";
import { stripeClient, stripeWebhooks } from "@/lib/billing/stripe";

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function orgIdFromMetadata(metadata: Stripe.Metadata | null | undefined): string | null {
  const value = metadata?.org_id;
  return typeof value === "string" && UUID_PATTERN.test(value) ? value : null;
}

function customerId(customer: string | { id: string } | null): string | null {
  if (typeof customer === "string") return customer;
  return customer?.id ?? null;
}

/** Plan as shown to admins: the price's nickname, falling back to its id. */
function planFromSubscription(subscription: Stripe.Subscription): string | null {
  const price = subscription.items?.data?.[0]?.price;
  return price?.nickname ?? price?.id ?? null;
}

/**
 * Resolve the org for a subscription event: the staff-copied metadata on
 * the subscription first, then the customer's own metadata via the API
 * when the client is configured.
 */
async function orgIdForSubscription(subscription: Stripe.Subscription): Promise<string | null> {
  const direct = orgIdFromMetadata(subscription.metadata);
  if (direct) return direct;
  const stripe = stripeClient();
  const customer = customerId(subscription.customer);
  if (!stripe || !customer) return null;
  const retrieved = await stripe.customers.retrieve(customer);
  if (retrieved.deleted) return null;
  return orgIdFromMetadata(retrieved.metadata);
}

/** Map a handled event to its upsert, or null for events we ignore. */
async function patchForEvent(
  event: Stripe.Event,
): Promise<{ orgId: string; patch: BillingSyncPatch } | null> {
  switch (event.type) {
    case "customer.subscription.updated":
    case "customer.subscription.deleted": {
      const subscription = event.data.object;
      const orgId = await orgIdForSubscription(subscription);
      const customer = customerId(subscription.customer);
      if (!orgId || !customer) return null;
      return {
        orgId,
        patch: {
          stripeCustomerId: customer,
          plan: planFromSubscription(subscription),
          status: subscription.status,
        },
      };
    }
    case "customer.updated": {
      const customer = event.data.object;
      const orgId = orgIdFromMetadata(customer.metadata);
      if (!orgId) return null;
      // Customer events carry no plan or status; keep the stored values.
      return { orgId, patch: { stripeCustomerId: customer.id } };
    }
    default:
      return null;
  }
}

export async function POST(request: Request): Promise<NextResponse> {
  const secret = process.env.STRIPE_WEBHOOK_SECRET;
  if (!secret) {
    return NextResponse.json({ error: "billing webhooks are not configured" }, { status: 503 });
  }

  const signature = request.headers.get("stripe-signature");
  const payload = await request.text();
  let event: Stripe.Event;
  try {
    event = stripeWebhooks().constructEvent(payload, signature ?? "", secret);
  } catch {
    return NextResponse.json({ error: "invalid signature" }, { status: 400 });
  }

  const resolved = await patchForEvent(event);
  if (resolved) {
    // A false return means the metadata named no known org: acknowledged
    // and ignored, same as an unhandled event type.
    await syncBillingAccount(resolved.orgId, resolved.patch);
  }
  return NextResponse.json({ received: true });
}
