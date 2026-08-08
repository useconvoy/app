import type { Metadata } from "next";

import { Chip } from "@/components/Chip";
import { listUserOrgs } from "@/lib/orgs/queries";
import { requireRoutinesPage } from "@/lib/routines/gate";
import { accountCopy, roleLabels } from "@/lexicon";

export const metadata: Metadata = { title: "Account" };
export const dynamic = "force-dynamic";

/**
 * The personal account surface: who this person is on the site, how they
 * sign in, and every organization they belong to. Read-only by design;
 * identity is owned by the sign-in provider and memberships are managed
 * in each organization's admin area.
 */
export default async function AccountPage() {
  const { session } = await requireRoutinesPage();
  const orgs = await listUserOrgs(session.userId);

  return (
    <div className="mx-auto max-w-3xl space-y-8">
      <header>
        <h1 className="font-display text-3xl text-ink">{accountCopy.profileTitle}</h1>
        <p className="mt-2 text-sm text-muted">{accountCopy.profileIntro}</p>
      </header>

      <section aria-label={accountCopy.profileTitle} className="rounded-lg border border-line bg-card p-6">
        <dl className="space-y-4">
          <div>
            <dt className="font-mono text-[11px] font-semibold uppercase tracking-[0.09em] text-muted">
              {accountCopy.nameLabel}
            </dt>
            <dd className="mt-1 text-sm text-ink">{session.name}</dd>
          </div>
          <div>
            <dt className="font-mono text-[11px] font-semibold uppercase tracking-[0.09em] text-muted">
              {accountCopy.emailLabel}
            </dt>
            <dd className="mt-1 text-sm text-ink">{session.email}</dd>
          </div>
          <div>
            <dt className="font-mono text-[11px] font-semibold uppercase tracking-[0.09em] text-muted">
              {accountCopy.signInLabel}
            </dt>
            <dd className="mt-1 text-sm text-ink">
              {session.workosUserId ? accountCopy.ssoLinked : accountCopy.ssoNotLinked}
            </dd>
          </div>
        </dl>
      </section>

      <section aria-label={accountCopy.membershipsTitle}>
        <h2 className="font-display text-lg text-ink">{accountCopy.membershipsTitle}</h2>
        <ul className="m-0 mt-3 list-none space-y-2 p-0">
          {orgs.map((org) => (
            <li
              key={org.id}
              className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line bg-card px-4 py-3"
            >
              <span className="text-sm text-ink">{org.name}</span>
              <span className="flex items-center gap-2">
                <Chip mono>{roleLabels[org.role] ?? org.role}</Chip>
                {org.id === session.orgId ? (
                  <Chip tone="pass" mono>
                    {accountCopy.currentOrgChip}
                  </Chip>
                ) : null}
              </span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
