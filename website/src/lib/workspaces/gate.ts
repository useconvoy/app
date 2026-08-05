/**
 * Page-level gate for workspace surfaces: only Admins and Operators
 * manage workspaces and systems. Server-side enforcement
 * lives here AND in every action; the nav lens is convenience only.
 */
import "server-only";

import { redirect } from "next/navigation";

import { getSession, type Session } from "@/lib/auth/session";
import { getMembership, type Membership } from "@/lib/orgs/queries";
import { can } from "@/lib/permissions";

export interface WorkspacePageContext {
  session: Session & { orgId: string };
  membership: Membership;
}

export async function requireWorkspacesPage(): Promise<WorkspacePageContext> {
  const session = await getSession();
  if (!session) redirect("/sign-in");
  if (!session.orgId) redirect("/switch");
  const membership = await getMembership(session.orgId, session.userId);
  if (
    !membership ||
    membership.status !== "active" ||
    !can("manage_workspaces", membership.role, membership.capabilities)
  ) {
    redirect("/app");
  }
  return { session: session as Session & { orgId: string }, membership };
}
