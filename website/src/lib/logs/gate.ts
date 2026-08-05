/**
 * Page-level gate for the Logs surface: Admin, Operator, and Member
 * only. Viewers are redirected server-side; the
 * nav lens hiding the item is convenience only.
 */
import "server-only";

import { redirect } from "next/navigation";

import { getSession, type Session } from "@/lib/auth/session";
import { getMembership, type Membership } from "@/lib/orgs/queries";

export interface LogsPageContext {
  session: Session & { orgId: string };
  membership: Membership;
}

export async function requireLogsPage(): Promise<LogsPageContext> {
  const session = await getSession();
  if (!session) redirect("/sign-in");
  if (!session.orgId) redirect("/switch");
  const membership = await getMembership(session.orgId, session.userId);
  if (!membership || membership.status !== "active" || membership.role === "viewer") {
    redirect("/app");
  }
  return { session: session as Session & { orgId: string }, membership };
}
