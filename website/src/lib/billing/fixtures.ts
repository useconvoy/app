/**
 * Fixture invoices for the unconfigured billing page, so the admin
 * surface is demoable before STRIPE_SECRET_KEY exists. Shapes mirror what
 * the page maps real Stripe invoices into; no Stripe ids or links are
 * invented (hosted links only exist for real invoices).
 */

export interface InvoiceRow {
  id: string;
  /** Human invoice number, mono fact. */
  number: string;
  amountUsd: number;
  status: string;
  createdAt: string;
  /** Stripe-hosted link; null for fixtures, which have nowhere to go. */
  hostedInvoiceUrl: string | null;
}

export const fixtureInvoices: InvoiceRow[] = [
  {
    id: "fixture-invoice-3",
    number: "CONVOY-0003",
    amountUsd: 1500,
    status: "paid",
    createdAt: "2026-07-01T09:00:00Z",
    hostedInvoiceUrl: null,
  },
  {
    id: "fixture-invoice-2",
    number: "CONVOY-0002",
    amountUsd: 1500,
    status: "paid",
    createdAt: "2026-06-01T09:00:00Z",
    hostedInvoiceUrl: null,
  },
  {
    id: "fixture-invoice-1",
    number: "CONVOY-0001",
    amountUsd: 750,
    status: "paid",
    createdAt: "2026-05-01T09:00:00Z",
    hostedInvoiceUrl: null,
  },
];
