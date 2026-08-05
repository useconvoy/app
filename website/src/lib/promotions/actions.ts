/**
 * Promotion flow actions: submit a rehearsal land report for review,
 * then promote or send back. Role checks are server-side; promote and
 * send-back are promoter-gated and both land an admin_audit row, since a
 * promotion decision is website-side governance, not a runtime write.
 */
"use server";

import { copy } from "@/lexicon";
import { routineIdForRun } from "@/lib/api/runs";
import { withOrgContext } from "@/lib/db";
import { can } from "@/lib/permissions";
import { isRehearsalRun } from "@/lib/routines/data";
import { requireRunContext } from "@/lib/runs/context";
import { notifyPromotionRequested } from "@/notifier/consumer";
import {
  decidePromotionRequest,
  requestPromotion,
  type PromotionRequest,
} from "./store";

export type PromotionSubmitResult =
  | { kind: "accepted"; requestId: string }
  | { kind: "refused"; message: string };

export type PromotionDecisionResult =
  | { kind: "accepted"; status: PromotionRequest["status"] }
  | { kind: "refused"; message: string }
  | { kind: "notFound" };

/**
 * Record the promotion request and notify promoters. Idempotent per
 * org + routine + run, so a double click cannot mint two reviews.
 */
export async function submitForPromotion(runId: string): Promise<PromotionSubmitResult> {
  const { session, membership } = await requireRunContext();
  if (!can("submit_promotion", membership.role, membership.capabilities)) {
    return { kind: "refused", message: copy.viewersCannotAct };
  }
  const routineId = routineIdForRun(runId);
  if (!routineId) {
    return { kind: "refused", message: copy.notTiedToRoutine };
  }
  if (!isRehearsalRun(runId)) {
    return { kind: "refused", message: copy.rehearsalOnlyPromotion };
  }
  const request = requestPromotion({
    orgId: session.orgId,
    routineId,
    runId,
    requestedBy: session.userId,
  });
  await notifyPromotionRequested(session.orgId, routineId, runId);
  return { kind: "accepted", requestId: request.id };
}

async function decide(
  requestId: string,
  decision: "promoted" | "sent_back",
  note?: string,
): Promise<PromotionDecisionResult> {
  const { session, membership } = await requireRunContext();
  if (!can("promote", membership.role, membership.capabilities)) {
    return { kind: "refused", message: copy.promotionNeedsPromoter };
  }
  if (decision === "sent_back" && !note?.trim()) {
    return { kind: "refused", message: copy.sendBackNoteRequired };
  }
  const settled = decidePromotionRequest(
    session.orgId,
    requestId,
    decision,
    session.userId,
    note?.trim(),
  );
  if (!settled) return { kind: "notFound" };
  await withOrgContext({ orgId: session.orgId, userId: session.userId }, (client) =>
    client.query(
      "INSERT INTO admin_audit (org_id, actor_id, action, subject) VALUES ($1, $2, $3, $4)",
      [
        session.orgId,
        session.userId,
        decision === "promoted" ? "promotion.promoted" : "promotion.sent_back",
        settled.id,
      ],
    ),
  );
  return { kind: "accepted", status: settled.status };
}

/** Promote: the rehearsal result may go live. Promoter-gated. */
export async function promoteRequest(requestId: string): Promise<PromotionDecisionResult> {
  return decide(requestId, "promoted");
}

/** Send back with a note saying what should change. Promoter-gated. */
export async function sendBackRequest(
  requestId: string,
  note: string,
): Promise<PromotionDecisionResult> {
  return decide(requestId, "sent_back", note);
}
