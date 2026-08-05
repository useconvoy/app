"use server";

import { z } from "zod";

const FIELD_NAMES = ["name", "email", "company", "handoff"] as const;
type FieldName = (typeof FIELD_NAMES)[number];

const demoRequestSchema = z.object({
  name: z.string().trim().min(1, "Please tell us your name."),
  email: z
    .string()
    .trim()
    .min(1, "Please tell us your work email.")
    .pipe(z.email("That does not look like an email address.")),
  company: z.string().trim().min(1, "Please tell us where you work."),
  handoff: z
    .string()
    .trim()
    .max(2000, "Please keep this under 2,000 characters.")
    .optional(),
});

export interface DemoFormState {
  status: "idle" | "invalid" | "submitted";
  errors: Partial<Record<FieldName, string>>;
  /** Echoed back for the confirmation message; nothing is persisted. */
  name?: string;
}

export async function requestDemo(
  _previous: DemoFormState,
  formData: FormData,
): Promise<DemoFormState> {
  const raw = Object.fromEntries(
    FIELD_NAMES.map((field) => {
      const value = formData.get(field);
      return [field, typeof value === "string" ? value : ""];
    }),
  );

  const parsed = demoRequestSchema.safeParse({
    ...raw,
    handoff: raw.handoff || undefined,
  });

  if (!parsed.success) {
    const fieldErrors = z.flattenError(parsed.error).fieldErrors;
    const errors: DemoFormState["errors"] = {};
    for (const field of FIELD_NAMES) {
      const first = fieldErrors[field]?.[0];
      if (first) errors[field] = first;
    }
    return { status: "invalid", errors };
  }

  // TODO(website-W6): hand the validated request to the CRM. Until then we
  // acknowledge the submission and deliberately store nothing.
  return { status: "submitted", errors: {}, name: parsed.data.name };
}
