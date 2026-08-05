import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { ensureRunRegistered, getRun, routineIdForRun } from "@/lib/api/runs";
import { can } from "@/lib/permissions";
import { submitForPromotion } from "@/lib/promotions/actions";
import { promotionRequestForRun } from "@/lib/promotions/store";
import {
  advanceClock,
  approvePlan,
  landRun,
  pauseRun,
  respondToGate,
  resumeRun,
  steerRun,
} from "@/lib/runs/commands";
import { requireRunPage } from "@/lib/runs/context";
import { RunDetail, type PromotionSummary } from "./run-detail";

export const metadata: Metadata = { title: "Run" };
export const dynamic = "force-dynamic";

/**
 * Server shell for the run detail: verifies the session, resolves the org's
 * tenant, and fetches the initial view through the single authenticated
 * edge. A 404 may mean wrong tenant and renders as not-found, full stop
 * (CLAUDE.md rule 7). The live timeline hydrates purely from the SSE
 * stream inside RunDetail; the W2 controls receive their session-bound
 * server actions here so every mutation carries the acting human.
 */
export default async function RunDetailPage({
  params,
}: {
  params: Promise<{ runId: string }>;
}) {
  const { runId } = await params;
  const { session, membership, actor } = await requireRunPage();
  const run = await getRun(actor, runId);
  if (!run) notFound();
  // A run first seen through its detail page joins the console's directory
  // so lists and the Checkpoints inbox can see it. TODO(runtime-D8): the
  // runtime's list endpoint retires this seam.
  ensureRunRegistered(runId, actor.tenantId);

  const routineId = routineIdForRun(runId) ?? null;
  const existing = routineId
    ? promotionRequestForRun(session.orgId, routineId, runId)
    : null;
  const promotion: PromotionSummary | null = existing
    ? { requestId: existing.id, status: existing.status }
    : null;

  return (
    <RunDetail
      initial={run}
      commands={{ pauseRun, resumeRun, landRun, steerRun, approvePlan, respondToGate, advanceClock }}
      viewer={{
        canAct: can("answer_checkpoints", membership.role, membership.capabilities),
        canSubmitPromotion:
          routineId !== null &&
          can("submit_promotion", membership.role, membership.capabilities),
        canExportEvidence: can("export_evidence", membership.role, membership.capabilities),
      }}
      promotion={promotion}
      submitPromotion={submitForPromotion}
    />
  );
}
