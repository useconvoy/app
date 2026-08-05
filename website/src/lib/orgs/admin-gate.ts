/**
 * Page-level admin gate. Server-side enforcement lives here AND in every
 * action; hiding the nav item is convenience only. Non-admins are sent
 * back to the portal, the org-less to the switcher, the signed-out to
 * sign-in.
 */
import "server-only";

import { redirect } from "next/navigation";

import { getSession, type Session } from "@/lib/auth/session";
import { can } from "@/lib/permissions";
import { getMembership, type Membership } from "./queries";

export interface AdminPageContext {
  session: Session & { orgId: string };
  membership: Membership;
}

export async function requireAdminPage(): Promise<AdminPageContext> {
  const session = await getSession();
  if (!session) redirect("/sign-in");
  if (!session.orgId) redirect("/switch");
  const membership = await getMembership(session.orgId, session.userId);
  if (
    !membership ||
    membership.status !== "active" ||
    !can("manage_members", membership.role, membership.capabilities)
  ) {
    redirect("/app");
  }
  return { session: session as Session & { orgId: string }, membership };
}
