/**
 * Server actions for feedback capture. Org and actor derive from the
 * verified session; the permissions matrix is re-checked server-side on
 * every call (give_feedback: everyone but viewers). Feedback bodies are
 * never logged and never quoted back in errors.
 */
"use server";

import { revalidatePath } from "next/cache";

import { routineIdForRun } from "@/lib/api/runs";
import { requireOrgSession } from "@/lib/auth/session";
import { getMembership } from "@/lib/orgs/queries";
import { can } from "@/lib/permissions";
import { feedbackKindLabels, type FeedbackKind } from "@/lexicon";
import { insertFeedback } from "./queries";

export interface SubmitFeedbackInput {
  runId: string;
  stepId?: string;
  kind: FeedbackKind;
  rating?: number;
  body?: string;
}

const MAX_BODY_LENGTH = 4000;

/**
 * Capture one piece of feedback on a run (optionally a step). Inserts with
 * learning_status "new"; the learning handoff moves it along later (D12).
 */
export async function submitFeedback(input: SubmitFeedbackInput): Promise<void> {
  const session = await requireOrgSession();
  const membership = await getMembership(session.orgId, session.userId);
  if (!membership || membership.status !== "active") {
    throw new Error("You are not an active member of this organization");
  }
  if (!can("give_feedback", membership.role, membership.capabilities)) {
    throw new Error("You cannot give feedback");
  }

  const runId = input.runId.trim();
  if (!runId) throw new Error("Feedback needs a run to attach to");
  if (!(input.kind in feedbackKindLabels)) throw new Error("Unknown feedback kind");

  const body = input.body?.trim() ?? "";
  if (body.length > MAX_BODY_LENGTH) throw new Error("Keep feedback under 4000 characters");

  let rating: number | undefined;
  if (input.kind === "rating") {
    rating = input.rating;
    if (!Number.isInteger(rating) || rating === undefined || rating < 1 || rating > 5) {
      throw new Error("Pick a star rating before sending");
    }
  } else if (body.length === 0) {
    throw new Error("Write your feedback before sending");
  }

  await insertFeedback(session.orgId, session.userId, {
    runId,
    stepId: input.stepId?.trim() || undefined,
    kind: input.kind,
    rating,
    body: body.length > 0 ? body : undefined,
  });

  revalidatePath(`/app/runs/${runId}`);
  const routineId = routineIdForRun(runId);
  if (routineId) revalidatePath(`/app/routines/${routineId}`);
  revalidatePath("/app/learning");
}

/**
 * FormData adapter for the composer's form posts. Bound to a run id by the
 * mounting page (`submitFeedbackForm.bind(null, runId)`), so the client
 * never chooses the run.
 */
export async function submitFeedbackForm(
  runId: string,
  stepId: string | null,
  formData: FormData,
): Promise<void> {
  const kind = String(formData.get("kind") ?? "");
  const ratingRaw = formData.get("rating");
  await submitFeedback({
    runId,
    stepId: stepId ?? undefined,
    kind: kind as FeedbackKind,
    rating: ratingRaw === null || ratingRaw === "" ? undefined : Number(ratingRaw),
    body: String(formData.get("body") ?? ""),
  });
}
