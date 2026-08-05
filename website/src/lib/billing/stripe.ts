/**
 * Env-gated Stripe access; billing is invoice-first. The secret key
 * never leaves process.env: it is read here, handed to the SDK, and never
 * logged, rendered, or stored: secrets never touch website code or the
 * website DB. When STRIPE_SECRET_KEY is
 * absent the whole billing surface degrades to the plain "handled by your
 * Convoy contact" state instead of erroring.
 */
import "server-only";

import Stripe from "stripe";

/** Whether the API-backed billing surfaces (portal, invoices) can work. */
export function stripeConfigured(): boolean {
  return Boolean(process.env.STRIPE_SECRET_KEY);
}

/** The API client, or null when billing is not configured. */
export function stripeClient(): Stripe | null {
  const key = process.env.STRIPE_SECRET_KEY;
  if (!key) return null;
  return new Stripe(key);
}

/**
 * Webhook signature verification needs no API key, only the SDK's HMAC
 * helpers, so it must not gate on STRIPE_SECRET_KEY: plan/status sync
 * works with just STRIPE_WEBHOOK_SECRET set. The constructor demands a
 * non-empty string; this placeholder is never sent anywhere because
 * constructEvent makes no network calls.
 */
export function stripeWebhooks(): Stripe["webhooks"] {
  return new Stripe("signature-verification-only").webhooks;
}
