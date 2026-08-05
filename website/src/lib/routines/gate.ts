/**
 * Page-level gate for routine surfaces: any active membership may view,
 * since every role may view routines. Server-side enforcement lives
 * here AND in every action; the nav lens is convenience only.
 */
import "server-only";

import { redirect } from "next/navigation";

import { getSession, type Session } from "@/lib/auth/session";
import { getMembership, type Membership } from "@/lib/orgs/queries";

export interface RoutinePageContext {
  session: Session & { orgId: string };
  membership: Membership;
}

export async function requireRoutinesPage(): Promise<RoutinePageContext> {
  const session = await getSession();
  if (!session) redirect("/sign-in");
  if (!session.orgId) redirect("/switch");
  const membership = await getMembership(session.orgId, session.userId);
  if (!membership || membership.status !== "active") redirect("/app");
  return { session: session as Session & { orgId: string }, membership };
}
