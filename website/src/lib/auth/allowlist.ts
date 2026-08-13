/**
 * The early-access gate: while the platform runs a waitlist, a NEW email
 * may sign in only when it is allowed. Three doors, checked in order:
 *
 *   1. CONVOY_ALLOWED_EMAILS, a comma-separated env list of full emails
 *      and "@domain.com" domain entries (bootstrap and break-glass);
 *   2. the signup_allowlist table, managed from the admin area;
 *   3. an account that already exists, so nobody who is already in gets
 *      locked out by the gate arriving after them.
 *
 * The matcher is pure and unit-tested; the database checks run without a
 * session (the gate fires before one exists).
 */
import "server-only";

import { withUserContext } from "@/lib/db";

/** Parse the env list: comma-separated, trimmed, lowercased, empties dropped. */
export function parseAllowedEntries(raw: string | undefined | null): string[] {
  return (raw ?? "")
    .split(",")
    .map((entry) => entry.trim().toLowerCase())
    .filter((entry) => entry.length > 0);
}

/** Whether one entry admits the email: "@domain" by suffix, else exact. */
export function entryAdmits(email: string, entry: string): boolean {
  const candidate = email.trim().toLowerCase();
  if (entry.startsWith("@")) return candidate.endsWith(entry);
  return candidate === entry;
}

export function emailAllowedByEntries(email: string, entries: string[]): boolean {
  return entries.some((entry) => entryAdmits(email, entry));
}

/** The full gate. */
export async function isSignupAllowed(email: string): Promise<boolean> {
  const candidate = email.trim().toLowerCase();
  if (candidate.length === 0) return false;
  if (emailAllowedByEntries(candidate, parseAllowedEntries(process.env.CONVOY_ALLOWED_EMAILS))) {
    return true;
  }
  const domain = candidate.slice(candidate.indexOf("@"));
  return withUserContext("", async (client) => {
    const { rows } = await client.query<{ allowed: boolean }>(
      `SELECT EXISTS (SELECT 1 FROM signup_allowlist WHERE email = $1 OR email = $2)
              OR signup_email_known($1) AS allowed`,
      [candidate, domain],
    );
    return rows[0]?.allowed ?? false;
  });
}
