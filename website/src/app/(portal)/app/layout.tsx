import Link from "next/link";
import { redirect } from "next/navigation";

import { AccountMenu } from "@/components/shell/AccountMenu";
import { BellButton } from "@/components/shell/BellButton";
import { CommandPalette } from "@/components/shell/CommandPalette";
import { NavItems } from "@/components/shell/NavItems";
import { OrgSwitcher } from "@/components/shell/OrgSwitcher";
import { PortalNavDrawer } from "@/components/shell/PortalNavDrawer";
import { routesForRole } from "@/components/shell/nav";
import { NoActiveOrgError, requireOrgSession, UnauthenticatedError } from "@/lib/auth/session";
import { getMembership, listUserOrgs } from "@/lib/orgs/queries";

/**
 * The portal shell: left nav, top bar (org switcher, command-K
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
  const orgOptions = orgs.map((org) => ({ id: org.id, name: org.name }));

  return (
    <div className="flex min-h-screen bg-field">
      {/* The fixed rail is a desktop shape; below md the drawer replaces it. */}
      <aside className="hidden w-60 shrink-0 flex-col gap-8 border-r border-line bg-card px-3 py-6 md:flex">
        <Link href="/app" className="px-3 font-display text-xl text-ink">
          Convoy
        </Link>
        <NavItems role={role} />
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between gap-3 border-b border-line bg-card px-4 py-2 md:gap-4 md:px-6 md:py-3">
          <div className="flex min-w-0 items-center gap-2">
            <PortalNavDrawer>
              <div className="border-b border-line-soft pb-4">
                <OrgSwitcher orgs={orgOptions} currentOrgId={session.orgId} />
              </div>
              <NavItems role={role} />
            </PortalNavDrawer>
            <Link href="/app" className="font-display text-lg text-ink md:hidden">
              Convoy
            </Link>
            <div className="hidden min-w-0 md:block">
              <OrgSwitcher orgs={orgOptions} currentOrgId={session.orgId} />
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-3">
            {/* The command-K jump is a keyboard affordance; on touch it is noise. */}
            <div className="hidden md:block">
              <CommandPalette routes={routesForRole(role)} />
            </div>
            <BellButton />
            <AccountMenu name={session.name} />
          </div>
        </header>
        {/* TODO(website): shell-level rehearsal framing. Pages that know
            they are in rehearsal context (run detail) mount their own
            RehearsalBanner today; this slot exists so the shell can take
            that over once rehearsal context is known at layout level. */}
        <div data-slot="rehearsal-banner" />
        <main className="flex-1 bg-field px-4 py-6 md:px-8 md:py-10">{children}</main>
      </div>
    </div>
  );
}
