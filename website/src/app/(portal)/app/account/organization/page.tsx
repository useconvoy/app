import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { friendlyDate } from "@/lib/format";
import { getOrgOverview } from "@/lib/orgs/queries";
import { can } from "@/lib/permissions";
import { requireRoutinesPage } from "@/lib/routines/gate";
import { accountCopy, roleLabels } from "@/lexicon";

export const metadata: Metadata = { title: "Organization" };
export const dynamic = "force-dynamic";

/**
 * The account view of the active organization: its name, this person's
 * role in it, how many people are in it, and when it was created. Admins
 * get the door to the admin area; everyone else reads.
 */
export default async function AccountOrganizationPage() {
  const { session, membership } = await requireRoutinesPage();
  const org = await getOrgOverview(session.orgId);
  if (!org) notFound();
  const isAdmin = can("manage_members", membership.role, membership.capabilities);

  return (
    <div className="mx-auto max-w-3xl space-y-8">
      <header>
        <p className="text-xs text-muted">
          <Link href="/app/account" className="underline">
            {accountCopy.profileTitle}
          </Link>
        </p>
        <h1 className="mt-1 font-display text-3xl text-ink">{accountCopy.organizationTitle}</h1>
        <p className="mt-2 text-sm text-muted">{accountCopy.organizationIntro}</p>
      </header>

      <section
        aria-label={accountCopy.organizationTitle}
        className="rounded-lg border border-line bg-card p-6"
      >
        <dl className="space-y-4">
          <div>
            <dt className="font-mono text-[11px] font-semibold uppercase tracking-[0.09em] text-muted">
              {accountCopy.nameLabel}
            </dt>
            <dd className="mt-1 text-sm text-ink">{org.name}</dd>
          </div>
          <div>
            <dt className="font-mono text-[11px] font-semibold uppercase tracking-[0.09em] text-muted">
              {accountCopy.yourRoleLabel}
            </dt>
            <dd className="mt-1 text-sm text-ink">
              {roleLabels[membership.role] ?? membership.role}
            </dd>
          </div>
          <div>
            <dt className="font-mono text-[11px] font-semibold uppercase tracking-[0.09em] text-muted">
              {accountCopy.memberCountLabel}
            </dt>
            <dd className="mt-1 text-sm text-ink">{accountCopy.peopleCount(org.memberCount)}</dd>
          </div>
          <div>
            <dt className="font-mono text-[11px] font-semibold uppercase tracking-[0.09em] text-muted">
              {accountCopy.createdLabel}
            </dt>
            <dd className="mt-1 font-mono text-xs uppercase text-ink">
              {friendlyDate(org.createdAt)}
            </dd>
          </div>
        </dl>
        {isAdmin ? (
          <p className="mt-6 border-t border-line-soft pt-4">
            <Link href="/app/admin" className="text-sm text-ink underline">
              {accountCopy.adminLink}
            </Link>
          </p>
        ) : null}
      </section>
    </div>
  );
}
