import type { Metadata } from "next";
import Link from "next/link";

import { requireAdminPage } from "@/lib/orgs/admin-gate";
import { getOrgSettings } from "@/lib/orgs/settings";
import { updateOrgPolicies } from "@/lib/orgs/settings-actions";
import { adminCopy } from "@/lexicon";
import { PoliciesForm } from "./PoliciesForm";

export const metadata: Metadata = { title: "Policies" };

/**
 * Org policies and budget defaults (DESIGN §5 Admin row, §9), stored in
 * organizations.settings (migration 0004). Admin lens; the action
 * re-checks manage_org_settings and writes the audit row.
 */
export default async function PoliciesPage() {
  const { session } = await requireAdminPage();
  const settings = await getOrgSettings(session.orgId);
  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <header>
        <p className="text-xs text-muted">
          <Link href="/app/admin" className="underline">
            Admin
          </Link>
        </p>
        <h1 className="mt-1 font-display text-2xl text-ink">{adminCopy.policiesTitle}</h1>
        <p className="mt-1 text-sm text-muted">{adminCopy.policiesIntro}</p>
      </header>
      <PoliciesForm initial={settings.policies} save={updateOrgPolicies} />
    </div>
  );
}
