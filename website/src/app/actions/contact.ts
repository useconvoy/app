"use server";

import { deliver, getDeliveryConfig } from "@/lib/contact/delivery";
import {
  EMPTY_VALUES,
  readValues,
  validate,
  type ContactErrors,
  type ContactValues,
} from "@/lib/contact/validate";

export type ContactStatus = "idle" | "invalid" | "unavailable" | "failed" | "sent";

export interface ContactState {
  status: ContactStatus;
  errors: ContactErrors;
  /** Echoed back so the form keeps what was typed on every non-success path. */
  values: ContactValues;
}

export async function sendContactRequest(
  _previous: ContactState,
  formData: FormData,
): Promise<ContactState> {
  const values = readValues(formData);

  // A filled honeypot is a bot, not a person; it gets the generic invalid
  // state rather than a hint about why.
  const trap = formData.get("website");
  if (typeof trap === "string" && trap.length > 0) {
    return { status: "invalid", errors: { email: "Please check this field." }, values };
  }

  const errors = validate(values);
  if (Object.keys(errors).length > 0) {
    return { status: "invalid", errors, values };
  }

  const config = getDeliveryConfig();
  if (!config.available) {
    // Nothing is stored or forwarded. The person is told exactly that and
    // keeps their text.
    return { status: "unavailable", errors: {}, values };
  }

  const result = await deliver(values, config);
  if (result.outcome === "sent") {
    return { status: "sent", errors: {}, values: EMPTY_VALUES };
  }
  console.error("contact delivery failed:", result.reason);
  return { status: "failed", errors: {}, values };
}
