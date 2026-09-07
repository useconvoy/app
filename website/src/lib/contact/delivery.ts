import "server-only";

import type { ContactValues } from "./validate";

/**
 * Where a validated contact request goes.
 *
 * No destination is configured yet: the receiving inbox has not been chosen
 * and no sending provider has been set up. Until both exist the form runs in
 * a preview state, and this module says so rather than pretending. Wiring a
 * provider means implementing `deliver` for it here, setting
 * CONTACT_DELIVERY to its id and CONTACT_TO_EMAIL to the inbox, and only
 * then does the page ever show the success state.
 *
 * Whatever is added must confirm the destination accepted the message
 * before returning `sent`. A queued-but-unconfirmed hand-off is `failed`.
 */
export type DeliveryResult = { outcome: "sent" } | { outcome: "failed"; reason: string };

export type DeliveryConfig = { available: false } | { available: true; provider: string; to: string };

export function getDeliveryConfig(): DeliveryConfig {
  const provider = process.env.CONTACT_DELIVERY?.trim();
  const to = process.env.CONTACT_TO_EMAIL?.trim();
  if (!provider || !to) return { available: false };
  return { available: true, provider, to };
}

export async function deliver(
  _request: ContactValues,
  config: Extract<DeliveryConfig, { available: true }>,
): Promise<DeliveryResult> {
  // No provider is implemented yet. Naming one in the environment without
  // code behind it must not look like success, so it fails loudly here and
  // the form reports that the message was not sent.
  return {
    outcome: "failed",
    reason: `Contact delivery provider "${config.provider}" is not implemented.`,
  };
}
