import Link from "next/link";
import { redirect } from "next/navigation";

import { BellButton } from "@/components/shell/BellButton";
import { CommandPalette } from "@/components/shell/CommandPalette";
import { NavItems } from "@/components/shell/NavItems";
import { OrgSwitcher } from "@/components/shell/OrgSwitcher";
import { routesForRole } from "@/components/shell/nav";
import { NoActiveOrgError, requireOrgSession, UnauthenticatedError } from "@/lib/auth/session";
import { getMembership, listUserOrgs } from "@/lib/orgs/queries";

/**
 * The portal shell (DESIGN §5): left nav, top bar (org switcher, command-K
 * jump, bell), content on field. The nav lens hides areas the role cannot
 * use; every page still enforces its own server-side check.
 */
export default async function PortalLayout({ children }: { children: React.ReactNode }) {
  let session;
  try {
    session = await requireOrgSession();
  } catch (error) {
    if (error instanceof UnauthenticatedError) redirect("/sign-in");
    if (error instanceof NoActiveOrgError) redirect("/onboarding");
    throw error;
  }

  const [membership, orgs] = await Promise.all([
    getMembership(session.orgId, session.userId),
    listUserOrgs(session.userId),
  ]);
  // A session pointing at an org the user no longer belongs to starts over.
  if (!membership) redirect("/onboarding");

  const role = membership.role;

  return (
    <div className="flex min-h-screen bg-field">
      <aside className="flex w-60 shrink-0 flex-col gap-8 border-r border-line bg-card px-3 py-6">
        <Link href="/app" className="px-3 font-display text-xl text-ink">
          Convoy
        </Link>
        <NavItems role={role} />
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between gap-4 border-b border-line bg-card px-6 py-3">
          <OrgSwitcher
            orgs={orgs.map((org) => ({ id: org.id, name: org.name }))}
            currentOrgId={session.orgId}
          />
          <div className="flex items-center gap-3">
            <CommandPalette routes={routesForRole(role)} />
            <BellButton />
          </div>
        </header>
        {/* TODO(website-Wn): RehearsalBanner from "@/components/RehearsalBanner"
            mounts in this slot when a rehearsal-context page renders. */}
        <div data-slot="rehearsal-banner" />
        <main className="flex-1 bg-field px-8 py-10">{children}</main>
      </div>
    </div>
  );
}
