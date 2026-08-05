/**
 * The billing surface, presentational: plan and status from
 * billing_accounts, invoices as Stripe-hosted links, and the portal
 * button when billing is connected. Unconfigured environments render the
 * plain "handled by your Convoy contact" state with sample invoices so
 * the page stays demoable; no error states, nothing key-shaped anywhere.
 */
import type { InvoiceRow } from "@/lib/billing/fixtures";
import { Chip } from "@/components/Chip";
import { money } from "@/lib/format";
import { friendlyDate } from "@/lib/format";
import { billingCopy } from "@/lexicon";

export interface BillingOverviewProps {
  plan: string | null;
  status: string | null;
  /** True when the Stripe API is reachable (portal + real invoices). */
  configured: boolean;
  invoices: InvoiceRow[];
  /** Server action opening the Stripe-hosted portal; absent when unconfigured. */
  manageAction?: () => Promise<void>;
}

export function BillingOverview({
  plan,
  status,
  configured,
  invoices,
  manageAction,
}: BillingOverviewProps) {
  return (
    <div className="space-y-6">
      <section className="rounded-md border border-line bg-card p-6">
        <div className="flex flex-wrap items-center gap-x-8 gap-y-3">
          <div>
            <p className="text-xs uppercase text-muted">{billingCopy.planLabel}</p>
            <p className="mt-1 text-sm text-ink">{plan ?? billingCopy.noPlanYet}</p>
          </div>
          <div>
            <p className="text-xs uppercase text-muted">{billingCopy.statusLabel}</p>
            <p className="mt-1">
              {status ? (
                <Chip tone={status === "active" ? "pass" : "hold"} mono>
                  {status}
                </Chip>
              ) : (
                <span className="text-sm text-muted">{billingCopy.noPlanYet}</span>
              )}
            </p>
          </div>
        </div>
        <p className="mt-4 text-sm text-muted">{billingCopy.handledByConvoy}</p>
        {configured && manageAction ? (
          <form action={manageAction} className="mt-4">
            <button
              type="submit"
              className="rounded-md border border-line bg-card px-3 py-1.5 text-sm font-medium text-ink hover:border-pine"
            >
              {billingCopy.managePayment}
            </button>
          </form>
        ) : null}
      </section>

      <section className="rounded-md border border-line bg-card p-6">
        <h2 className="text-base font-medium text-ink">{billingCopy.invoicesTitle}</h2>
        {!configured && invoices.length > 0 ? (
          <p className="mt-1 text-xs text-muted">{billingCopy.sampleInvoicesNote}</p>
        ) : null}
        {invoices.length === 0 ? (
          <p className="mt-3 text-sm text-muted">{billingCopy.noInvoicesYet}</p>
        ) : (
          <ul className="mt-4 m-0 list-none divide-y divide-line-soft p-0">
            {invoices.map((invoice) => (
              <li key={invoice.id} className="flex flex-wrap items-center gap-3 py-3">
                <span className="font-mono text-xs text-ink">{invoice.number}</span>
                <span className="font-mono text-xs text-muted">{friendlyDate(invoice.createdAt)}</span>
                <span className="font-mono text-xs text-ink">{money(invoice.amountUsd)}</span>
                <Chip tone={invoice.status === "paid" ? "pass" : "hold"} mono>
                  {invoice.status}
                </Chip>
                {invoice.hostedInvoiceUrl ? (
                  <a
                    href={invoice.hostedInvoiceUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="text-xs text-ink underline hover:text-pine"
                  >
                    {billingCopy.viewInvoice}
                  </a>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
