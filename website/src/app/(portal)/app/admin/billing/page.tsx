import type { Metadata } from "next";
import Link from "next/link";

import { openBillingPortal } from "@/lib/billing/actions";
import { fixtureInvoices, type InvoiceRow } from "@/lib/billing/fixtures";
import { getBillingAccount } from "@/lib/billing/queries";
import { stripeClient } from "@/lib/billing/stripe";
import { requireAdminPage } from "@/lib/orgs/admin-gate";
import { billingCopy } from "@/lexicon";
import { BillingOverview } from "./BillingOverview";

export const metadata: Metadata = { title: "Billing" };

/**
 * Admin billing (DESIGN §8, invoice-first D11): plan and status read from
 * billing_accounts (synced by webhook), invoices listed straight from
 * Stripe as hosted links, payment method managed in the Stripe-hosted
 * portal. Everything API-backed is env-gated; without STRIPE_SECRET_KEY
 * the page renders the plain Convoy-contact state over sample invoices.
 */
export default async function BillingPage() {
  const { session } = await requireAdminPage();
  const account = await getBillingAccount(session.orgId);
  const stripe = stripeClient();
  const configured = stripe !== null && Boolean(account?.stripeCustomerId);

  let invoices: InvoiceRow[] = fixtureInvoices;
  if (stripe && account?.stripeCustomerId) {
    try {
      const listed = await stripe.invoices.list({ customer: account.stripeCustomerId, limit: 12 });
      invoices = listed.data.map((invoice) => ({
        id: invoice.id ?? invoice.number ?? "invoice",
        number: invoice.number ?? "Draft",
        amountUsd: (invoice.total ?? 0) / 100,
        status: invoice.status ?? "open",
        createdAt: new Date(invoice.created * 1000).toISOString(),
        hostedInvoiceUrl: invoice.hosted_invoice_url ?? null,
      }));
    } catch {
      // Stripe being unreachable must not break the admin surface; the
      // page quietly shows no invoices rather than an error.
      invoices = [];
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <header>
        <p className="text-xs text-muted">
          <Link href="/app/admin" className="underline">
            Admin
          </Link>
        </p>
        <h1 className="mt-1 font-display text-2xl text-ink">{billingCopy.title}</h1>
        <p className="mt-1 text-sm text-muted">{billingCopy.intro}</p>
      </header>
      <BillingOverview
        plan={account?.plan ?? null}
        status={account?.status ?? null}
        configured={configured}
        invoices={invoices}
        manageAction={configured ? openBillingPortal : undefined}
      />
    </div>
  );
}
