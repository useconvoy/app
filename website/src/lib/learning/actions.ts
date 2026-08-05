/**
 * Server actions for the learning queue. Approving and shipping are gated
 * by ship_improvements (Admins and Operators; Members via the grantable
 * capability), re-checked server-side on every call. Approving an
 * improvement is the learning handoff moment: the routine's "new" feedback is
 * queued for learning alongside it; shipping consumes what was queued.
 * TODO(learning): the service takes over both transitions when it exists.
 */
"use server";

import { revalidatePath } from "next/cache";

import { learningClient } from "@/lib/api/learning";
import { requireOrgSession } from "@/lib/auth/session";
import { consumeRoutineFeedback, queueRoutineFeedback } from "@/lib/feedback/queries";
import { getMembership } from "@/lib/orgs/queries";
import { can } from "@/lib/permissions";

async function requireShipper() {
  const session = await requireOrgSession();
  const membership = await getMembership(session.orgId, session.userId);
  if (!membership || membership.status !== "active") {
    throw new Error("You are not an active member of this organization");
  }
  if (!can("ship_improvements", membership.role, membership.capabilities)) {
    throw new Error("You cannot approve or ship improvements");
  }
  return { session, membership };
}

/** Approve a proposed improvement and queue its routine's feedback. */
export async function approveImprovement(id: string): Promise<void> {
  const { session } = await requireShipper();
  const improvement = await learningClient().approveImprovement(id);
  await queueRoutineFeedback(session.orgId, improvement.routineId);
  revalidatePath("/app/learning");
}

/** Ship an approved improvement and consume the feedback it absorbed. */
export async function shipImprovement(id: string): Promise<void> {
  const { session } = await requireShipper();
  const improvement = await learningClient().shipImprovement(id);
  await consumeRoutineFeedback(session.orgId, improvement.routineId);
  revalidatePath("/app/learning");
  revalidatePath(`/app/routines/${improvement.routineId}`);
}
