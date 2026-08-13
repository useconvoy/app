/**
 * Server actions for the early-access allowlist: which emails may sign in
 * while the platform runs a waitlist. Admin-only (manage_org_settings),
 * every change writes an admin_audit row. Entries are lowercased full
 * emails or "@domain.com" domain entries; existing accounts are never
 * affected (the gate only fires for new identities).
 */
"use server";

import { revalidatePath } from "next/cache";

import { requireOrgSession } from "@/lib/auth/session";
import { withOrgContext } from "@/lib/db";
import { can } from "@/lib/permissions";
import { getMembership } from "./queries";

export interface AccessActionResult {
  ok: boolean;
  message: string;
}

const ENTRY_PATTERN = /^(@[a-z0-9.-]+\.[a-z]{2,}|[^\s@]+@[a-z0-9.-]+\.[a-z]{2,})$/;

async function requireAccessAdmin() {
  const session = await requireOrgSession();
  const membership = await getMembership(session.orgId, session.userId);
  if (
    !membership ||
    membership.status !== "active" ||
    !can("manage_org_settings", membership.role, membership.capabilities)
  ) {
    throw new Error("Only organization admins can manage early access");
  }
  return session;
}

export async function addAllowedEmail(
  rawEntry: string,
  note: string,
): Promise<AccessActionResult> {
  const session = await requireAccessAdmin();
  const entry = rawEntry.trim().toLowerCase();
  if (!ENTRY_PATTERN.test(entry)) {
    return {
      ok: false,
      message: "Enter a full email, or @domain.com to admit a whole domain.",
    };
  }
  await withOrgContext({ orgId: session.orgId, userId: session.userId }, async (client) => {
    await client.query(
      `INSERT INTO signup_allowlist (email, note, added_by)
       VALUES ($1, $2, $3) ON CONFLICT (email) DO NOTHING`,
      [entry, note.trim() || null, session.userId],
    );
    await client.query(
      "INSERT INTO admin_audit (org_id, actor_id, action, subject) VALUES ($1, $2, $3, $4)",
      [session.orgId, session.userId, "access.email_allowed", entry],
    );
  });
  revalidatePath("/app/admin/access");
  return { ok: true, message: `${entry} can sign in now.` };
}

export async function removeAllowedEmail(entry: string): Promise<AccessActionResult> {
  const session = await requireAccessAdmin();
  await withOrgContext({ orgId: session.orgId, userId: session.userId }, async (client) => {
    await client.query("DELETE FROM signup_allowlist WHERE email = $1", [entry]);
    await client.query(
      "INSERT INTO admin_audit (org_id, actor_id, action, subject) VALUES ($1, $2, $3, $4)",
      [session.orgId, session.userId, "access.email_removed", entry],
    );
  });
  revalidatePath("/app/admin/access");
  return {
    ok: true,
    message: `${entry} removed. Anyone already signed up keeps their account.`,
  };
}
