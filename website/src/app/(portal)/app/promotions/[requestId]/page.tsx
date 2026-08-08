import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";

import { LandReportSection } from "@/components/LandReportSection";
import { checkpointKinds, copy, promotionStatusLabels } from "@/lexicon";
import { getRun } from "@/lib/api/runs";
import { can } from "@/lib/permissions";
import { promoteRequest, sendBackRequest } from "@/lib/promotions/actions";
import { getPromotionRequest } from "@/lib/promotions/store";
import { getRoutine } from "@/lib/routines/queries";
import { requireRunPage } from "@/lib/runs/context";
import { PromotionDecision } from "./promotion-decision";

export const metadata: Metadata = { title: "Promotion review" };
export const dynamic = "force-dynamic";

/**
 * The promotion review page: the rehearsal's land report in full,
 * with the stand-in outbox, then the promote checkpoint. Reached from the
 * Checkpoints inbox and the promotion-requested notification. Store
 * lookups are org-scoped: another org's request id is simply not found.
 */
export default async function PromotionReviewPage({
  params,
}: {
  params: Promise<{ requestId: string }>;
}) {
  const { requestId } = await params;
  const { session, membership, actor } = await requireRunPage();
  if (membership.role === "viewer") redirect("/app");

  const request = getPromotionRequest(session.orgId, requestId);
  if (!request) notFound();
  const run = await getRun(actor, request.runId);
  if (!run) notFound();

  const routine = await getRoutine(session.orgId, request.routineId);
  const report = (run.land_report as Record<string, unknown> | null) ?? null;
  const canPromote = can("promote", membership.role, membership.capabilities);
  const settled = request.status !== "requested";

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6">
      <header className="flex flex-wrap items-center gap-3">
        <h1 className="font-display text-3xl text-ink">{copy.promotionReviewTitle}</h1>
        <span className="rounded-full border border-line bg-card px-2 py-0.5 font-mono text-xs uppercase tracking-wide text-muted">
          {promotionStatusLabels[request.status]}
        </span>
      </header>
      <p className="text-muted">
        {routine?.name ?? run.goal} · {checkpointKinds.promotion.description}
      </p>

      {report ? (
        <LandReportSection
          report={report}
          budget={run.budget ?? null}
          steps={run.steps ?? []}
          rehearsal
        >
          {settled ? (
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <span
                className={[
                  "rounded-full border px-2 py-0.5 font-mono text-xs uppercase tracking-wide",
                  request.status === "promoted"
                    ? "border-pass-soft bg-pass-soft text-pass-text"
                    : "border-hold-soft bg-hold-soft text-hold-text",
                ].join(" ")}
              >
                {promotionStatusLabels[request.status]}
              </span>
              {request.note && <span className="text-ink">{request.note}</span>}
            </div>
          ) : (
            <PromotionDecision
              requestId={request.id}
              canPromote={canPromote}
              actions={{ promote: promoteRequest, sendBack: sendBackRequest }}
            />
          )}
        </LandReportSection>
      ) : (
        <p className="text-sm text-muted">The land report for this run is not ready yet.</p>
      )}

      <p>
        <a
          href={`/api/runs/${run.run_id}/evidence`}
          download
          className="inline-flex items-center rounded-md border border-line bg-card px-3 py-1.5 text-sm font-medium text-ink hover:bg-field"
        >
          {copy.exportEvidenceBinder}
        </a>
      </p>
    </div>
  );
}
