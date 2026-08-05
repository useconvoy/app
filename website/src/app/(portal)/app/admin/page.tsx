import type { Metadata } from "next";
import Link from "next/link";

import { requireAdminPage } from "@/lib/orgs/admin-gate";
import { listInvites, listMembers, listTeams } from "@/lib/orgs/queries";

export const metadata: Metadata = { title: "Admin" };

export default async function AdminPage() {
  const { session } = await requireAdminPage();
  const [members, teams, invites] = await Promise.all([
    listMembers(session.orgId),
    listTeams(session.orgId),
    listInvites(session.orgId),
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
