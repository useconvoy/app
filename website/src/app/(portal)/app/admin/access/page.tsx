import type { Metadata } from "next";
import Link from "next/link";

import { requireAdminPage } from "@/lib/orgs/admin-gate";
import { addAllowedEmail, removeAllowedEmail } from "@/lib/orgs/access-actions";
import { withOrgContext } from "@/lib/db";
import { friendlyDate } from "@/lib/format";
import { AccessManager, type AllowlistEntry } from "./AccessManager";

export const metadata: Metadata = { title: "Early access" };
export const dynamic = "force-dynamic";

/**
 * The early-access list: which emails may sign in while the platform runs
 * a waitlist. Entries are full emails or whole domains; people who
 * already have an account are never affected. Admin-only, audited.
 */
export default async function AccessPage() {
  const { session } = await requireAdminPage();
  const entries = await withOrgContext(
    { orgId: session.orgId, userId: session.userId },
    async (client) => {
      const { rows } = await client.query<{
        email: string;
        note: string | null;
        created_at: string;
      }>("SELECT email, note, created_at FROM signup_allowlist ORDER BY created_at DESC");
      return rows.map<AllowlistEntry>((row) => ({
        entry: row.email,
        note: row.note,
        added: friendlyDate(row.created_at),
      }));
    },
  );
  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <header>
        <p className="text-xs text-muted">
          <Link href="/app/admin" className="underline">
            Admin
          </Link>
        </p>
        <h1 className="mt-1 font-display text-2xl text-ink">Early access</h1>
        <p className="mt-1 text-sm text-muted">
          While Convoy runs a waitlist, only these emails can sign in. Add a full email, or
          @domain.com to admit a whole domain. People who already have an account always keep
          their access.
        </p>
      </header>
      <AccessManager entries={entries} add={addAllowedEmail} remove={removeAllowedEmail} />
    </div>
  );
}
