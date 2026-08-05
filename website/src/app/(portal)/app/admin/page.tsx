import type { Metadata } from "next";
import Link from "next/link";

import { getBillingAccount } from "@/lib/billing/queries";
import { money } from "@/lib/format";
import { requireAdminPage } from "@/lib/orgs/admin-gate";
import { listInvites, listMembers, listTeams } from "@/lib/orgs/queries";
import { getOrgSettings } from "@/lib/orgs/settings";
import { adminCopy, billingCopy } from "@/lexicon";

export const metadata: Metadata = { title: "Admin" };

/**
 * The Admin index: members and teams, policies & budget defaults,
 * notification defaults, billing, API access, and the org audit log.
 */
export default async function AdminPage() {
  const { session } = await requireAdminPage();
  const [members, teams, invites, settings, billing] = await Promise.all([
    listMembers(session.orgId),
    listTeams(session.orgId),
    listInvites(session.orgId),
    getOrgSettings(session.orgId),
    getBillingAccount(session.orgId),
  ]);
  const pendingInvites = invites.filter((invite) => invite.status === "pending").length;
  const sections = [
    {
      href: "/app/admin/members",
      title: "Members",
      description: "People, roles, capabilities, and invites.",
      fact: `${members.length} members · ${pendingInvites} pending invites`,
    },
    {
      href: "/app/admin/teams",
      title: "Teams",
      description: "Groups for checkpoint routing and filters.",
      fact: `${teams.length} teams`,
    },
    {
      href: "/app/admin/policies",
      title: adminCopy.policiesTitle,
      description: adminCopy.policiesIntro,
      fact: `Run cap ${money(settings.policies.defaultRunBudgetCapUsd)} default`,
    },
    {
      href: "/app/admin/notifications",
      title: adminCopy.notificationDefaultsTitle,
      description: adminCopy.notificationDefaultsIntro,
      fact: "In app on for every class",
    },
    {
      href: "/app/admin/billing",
      title: billingCopy.title,
      description: billingCopy.handledByConvoy,
      fact: billing?.plan ? `${billing.plan} · ${billing.status ?? ""}`.trim() : "Invoice-first",
    },
    {
      href: "/app/admin/api-access",
      title: adminCopy.apiAccessTitle,
      description: adminCopy.apiAccessIntro,
      fact: "Managed by Convoy",
    },
    {
      href: "/app/admin/audit",
      title: adminCopy.auditTitle,
      description: adminCopy.auditIntro,
      fact: "Newest first",
    },
  ];
  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-display text-2xl text-ink">Admin</h1>
        <p className="mt-1 text-sm text-muted">Manage who can do what in this organization.</p>
      </header>
      <div className="grid gap-4 sm:grid-cols-2">
        {sections.map((section) => (
          <Link
            key={section.href}
            href={section.href}
            className="block rounded-md border border-line bg-card p-6 hover:border-pine"
          >
            <h2 className="text-base font-medium text-ink">{section.title}</h2>
            <p className="mt-1 text-sm text-muted">{section.description}</p>
            <p className="mt-4 font-mono text-xs uppercase text-muted">{section.fact}</p>
          </Link>
        ))}
      </div>
    </div>
  );
}
