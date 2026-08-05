/**
 * Shared page/action context for run surfaces: the verified session, the
 * active membership (for server-side role checks), and the ActorContext
 * the control-plane client rides. The actor id is the session email so the
 * runtime's audit trail and timeline attribute actions to a readable human
 * identity; when OIDC lands at the edge this bridge disappears.
 */
import "server-only";

import { redirect } from "next/navigation";

import type { ActorContext } from "@/lib/api/client";
import {
  NoActiveOrgError,
  requireOrgSession,
  UnauthenticatedError,
  type Session,
} from "@/lib/auth/session";
import { getMembership, type Membership } from "@/lib/orgs/queries";
import { orgTenantId } from "@/lib/routines/queries";

export interface RunContext {
  session: Session & { orgId: string };
  membership: Membership;
  actor: ActorContext;
}

export async function requireRunContext(): Promise<RunContext> {
  const session = await requireOrgSession();
  const [membership, tenantId] = await Promise.all([
    getMembership(session.orgId, session.userId),
    orgTenantId(session.orgId),
  ]);
  if (!membership || membership.status !== "active") {
    throw new Error("You are not an active member of this organization");
  }
  return { session, membership, actor: { actorId: session.email, tenantId } };
}

/** Page-level gate: like requireRunContext, but redirects instead of throwing. */
export async function requireRunPage(): Promise<RunContext> {
  try {
    return await requireRunContext();
  } catch (error) {
    if (error instanceof UnauthenticatedError) redirect("/sign-in");
    if (error instanceof NoActiveOrgError) redirect("/onboarding");
    // Stale membership or a vanished org: start over at onboarding.
    redirect("/onboarding");
  }
}
