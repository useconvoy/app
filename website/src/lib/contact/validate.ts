/**
 * Contact request validation. Runs on the server for every submission; the
 * browser gets the same rules through native attributes for early hints.
 * Kept dependency-free: five fields do not need a schema library.
 */
export const CONTACT_FIELDS = ["email", "company", "blocker", "name", "hardware"] as const;
export type ContactField = (typeof CONTACT_FIELDS)[number];

export type ContactValues = Record<ContactField, string>;
export type ContactErrors = Partial<Record<ContactField, string>>;

export const EMPTY_VALUES: ContactValues = {
  email: "",
  company: "",
  blocker: "",
  name: "",
  hardware: "",
};

export const LIMITS = {
  email: 254,
  company: 200,
  blocker: 4000,
  name: 200,
  hardware: 500,
} as const;

/* Practical shape check, not RFC 5322: one @, something either side, a dot
 * in the domain. Anything stricter rejects real addresses. */
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function readValues(formData: FormData): ContactValues {
  const values = {} as ContactValues;
  for (const field of CONTACT_FIELDS) {
    const raw = formData.get(field);
    values[field] = typeof raw === "string" ? raw : "";
  }
  return values;
}

export function validate(values: ContactValues): ContactErrors {
  const errors: ContactErrors = {};
  const email = values.email.trim();
  const company = values.company.trim();
  const blocker = values.blocker.trim();

  if (!email) errors.email = "Please enter your work email.";
  else if (email.length > LIMITS.email || !EMAIL.test(email))
    errors.email = "That does not look like an email address.";

  if (!company) errors.company = "Please tell us your company or team.";
  else if (company.length > LIMITS.company)
    errors.company = `Please keep this under ${LIMITS.company} characters.`;

  if (!blocker) errors.blocker = "Please describe what you are deploying and what is blocking you.";
  else if (blocker.length > LIMITS.blocker)
    errors.blocker = `Please keep this under ${LIMITS.blocker.toLocaleString("en-US")} characters.`;

  if (values.name.trim().length > LIMITS.name)
    errors.name = `Please keep this under ${LIMITS.name} characters.`;
  if (values.hardware.trim().length > LIMITS.hardware)
    errors.hardware = `Please keep this under ${LIMITS.hardware} characters.`;

  return errors;
}
