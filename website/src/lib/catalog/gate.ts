/**
 * Page-level gate for catalog surfaces: the storefront and install flow
 * live behind the Operator lens (DESIGN §6: phase-1 installs are performed
 * by staff Operators through the same screens customers will use later),
 * so Admin and Operator only. Publishing is checked separately with
 * can("publish_catalog") where the affordance renders and again in the
 * action. Server-side enforcement lives here AND in every action; the
 * routines-list button is convenience only.
 */
import "server-only";

import { redirect } from "next/navigation";

import { getSession, type Session } from "@/lib/auth/session";
import { getMembership, type Membership } from "@/lib/orgs/queries";
import { can } from "@/lib/permissions";

export interface CatalogPageContext {
  session: Session & { orgId: string };
  membership: Membership;
}

export async function requireCatalogPage(): Promise<CatalogPageContext> {
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
