/**
 * Billing server actions (DESIGN §8): the one mutation is opening the
 * Stripe-hosted portal for payment details. Env-gated; admin-only via the
 * same org-settings permission that guards the billing page.
 */
"use server";

import { headers } from "next/headers";
import { redirect } from "next/navigation";

import { requireOrgSession } from "@/lib/auth/session";
import { getMembership } from "@/lib/orgs/queries";
import { can } from "@/lib/permissions";
import { getBillingAccount } from "./queries";
import { stripeClient } from "./stripe";

/**
 * Create a Stripe billing-portal session and send the admin there. The
 * return URL comes from the request's own host so local and deployed
 * environments both round-trip.
 */
export async function openBillingPortal(): Promise<void> {
  const session = await requireOrgSession();
  const membership = await getMembership(session.orgId, session.userId);
  if (
    !membership ||
    membership.status !== "active" ||
    !can("manage_org_settings", membership.role, membership.capabilities)
  ) {
    throw new Error("Only organization admins can manage billing");
  }
  const stripe = stripeClient();
  const account = await getBillingAccount(session.orgId);
  if (!stripe || !account?.stripeCustomerId) {
    throw new Error("Billing is handled by your Convoy contact");
  }
  const requestHeaders = await headers();
  const host = requestHeaders.get("host") ?? "localhost:3000";
  const proto = requestHeaders.get("x-forwarded-proto") ?? "http";
  const portal = await stripe.billingPortal.sessions.create({
    customer: account.stripeCustomerId,
    return_url: `${proto}://${host}/app/admin/billing`,
  });
  redirect(portal.url);
}
