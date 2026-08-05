"use client";

/**
 * Run detail: plan board, live timeline hydrated purely from
 * the SSE stream (seq 0 replays everything), and the vitals rail. The
 * rehearsal variant branches on the stream's sandbox flag:
 * graphite/dashed treatment, rehearsal banner, virtual time primary.
 * Production is unlabeled, always.
 */
import Link from "next/link";
import { useMemo, useState } from "react";

import { BudgetMeter } from "@/components/BudgetMeter";
import { Button } from "@/components/Button";
import { DisconnectBanner } from "@/components/DisconnectBanner";
import { LandReportSection } from "@/components/LandReportSection";
import { RehearsalBanner } from "@/components/RehearsalBanner";
import { RouteStepList } from "@/components/RouteStepList";
import { StatusChip } from "@/components/StatusChip";
import { TimelineRow } from "@/components/TimelineRow";
import {
  checkpointKinds,
  copy,
  promotionStatusLabels,
  stepStatusLabels,
  type PromotionStatus,
} from "@/lexicon";
import type { RunView } from "@/lib/api/client";
import { friendlyDateTime, money } from "@/lib/format";
import type { PromotionSubmitResult } from "@/lib/promotions/actions";
import type { RunCommandHandlers } from "@/lib/runs/command-types";
import {
  childRuns,
  heldKind,
  isRehearsal,
  latestBudget,
  latestLandReport,
  latestPlan,
  latestRunStatus,
  planBoardSteps,
  routeStateFor,
  stepStatuses,
  type ChildRunLine,
  type RunStreamEvent,
} from "@/lib/runs/status";
import { RunControls, type BlockedStep } from "./run-controls";
import { useRunEvents } from "./use-run-events";

function planVersion(plan: RunView["plan"]): number | null {
  const version = (plan as Record<string, unknown> | null)?.["version"];
  return typeof version === "number" ? version : null;
}

function workspaceIdFrom(events: readonly RunStreamEvent[]): string | null {
  const started = events.find((event) => event.type === "run_started");
  const id = started?.payload["environment_id"];
  return typeof id === "string" ? id : null;
}

/**
 * Steps holding at a checkpoint right now, from confirmed events: opened
 * gates minus answered/timed-out ones. Before the stream's replay arrives,
 * the fetched view's flat steps stand in so the respond form still mounts.
 */
function openBlockedSteps(events: readonly RunStreamEvent[], initial: RunView): BlockedStep[] {
  if (events.length === 0) {
    return (initial.steps ?? [])
      .filter((step) => step.status === "blocked_on_human")
      .map((step) => ({ stepId: step.step_id, prompt: step.description ?? "" }));
  }
  const open = new Map<string, BlockedStep>();
  for (const event of events) {
    const stepId = event.payload["step_id"];
    if (typeof stepId !== "string") continue;
    if (event.type === "gate_opened") {
      const prompt = event.payload["prompt"];
      const onTimeout = event.payload["on_timeout"];
      open.set(stepId, {
        stepId,
        prompt: typeof prompt === "string" ? prompt : "",
        onTimeout:
          onTimeout === "pause" || onTimeout === "skip" || onTimeout === "fail"
            ? onTimeout
            : null,
      });
    } else if (event.type === "gate_answered" || event.type === "gate_timed_out") {
      open.delete(stepId);
    }
  }
  return [...open.values()];
}

/** The run's current virtual moment, when it runs a virtual clock. */
function latestVirtualTs(events: readonly RunStreamEvent[]): string | null {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const ts = events[i]!.virtual_ts;
    if (typeof ts === "string") return ts;
  }
  return null;
}

export interface RunViewerContext {
  /** Whether the viewer may act on runs at all (member and up). */
  canAct: boolean;
  canSubmitPromotion: boolean;
  canExportEvidence: boolean;
}

export interface PromotionSummary {
  requestId: string;
  status: PromotionStatus;
}

/**
 * The promotion block on a rehearsal land report: submit for review,
 * or the request's current standing with a link to the review page.
 */
function PromotionBlock({
  runId,
  promotion,
  submitPromotion,
}: {
  runId: string;
  promotion: PromotionSummary | null;
  submitPromotion?: (runId: string) => Promise<PromotionSubmitResult>;
}) {
  const [request, setRequest] = useState<PromotionSummary | null>(promotion);
  const [message, setMessage] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit() {
    if (!submitPromotion || pending) return;
    setPending(true);
    setMessage(null);
    const result = await submitPromotion(runId);
    if (result.kind === "accepted") {
      setRequest({ requestId: result.requestId, status: "requested" });
      setMessage(copy.promotionRequestedNote);
    } else {
      setMessage(result.message);
    }
    setPending(false);
  }

  return (
    <div id="promote" className="flex flex-wrap items-center gap-3">
      {request ? (
        <>
          <span className="font-mono text-xs uppercase tracking-wide text-muted">
            {promotionStatusLabels[request.status]}
          </span>
          <Link
            href={`/app/promotions/${request.requestId}`}
            className="text-sm text-ink underline underline-offset-2"
          >
            {copy.promotionReviewTitle}
          </Link>
        </>
      ) : (
        <Button disabled={pending} onClick={() => void submit()}>
          {copy.submitForPromotion}
        </Button>
      )}
      {message && (
        <p role="status" className="w-full text-sm text-muted">
          {message}
        </p>
      )}
    </div>
  );
}

/** Fan-out compaction: one group line per helper run, linking through to its own detail. */
function FanoutGroup({ children }: { children: ChildRunLine[] }) {
  return (
    <li className="my-2 rounded-md border border-line-soft bg-field px-3 py-2">
      <p className="font-mono text-xs uppercase tracking-wide text-muted">{copy.helperRuns}</p>
      <ul className="mt-1 space-y-1">
        {children.map((child) => (
          <li key={child.childRunId} className="flex flex-wrap items-baseline gap-2 text-sm">
            <Link
              href={`/app/runs/${child.childRunId}`}
              className="text-ink underline-offset-2 hover:underline"
            >
              {child.headline}
            </Link>
            <span aria-hidden="true" className="text-muted">
              ·
            </span>
            <span className="font-mono text-xs text-ink">{money(child.costUsd)}</span>
            <span aria-hidden="true" className="text-muted">
              ·
            </span>
            <span className="font-mono text-xs uppercase tracking-wide text-muted">
              {stepStatusLabels[child.status] ?? child.status}
            </span>
          </li>
        ))}
      </ul>
    </li>
  );
}

export function RunDetail({
  initial,
  commands,
  viewer,
  promotion = null,
  submitPromotion,
}: {
  initial: RunView;
  /** Session-bound server actions, wired in by the page. */
  commands?: Partial<RunCommandHandlers>;
  viewer?: RunViewerContext;
  promotion?: PromotionSummary | null;
  submitPromotion?: (runId: string) => Promise<PromotionSubmitResult>;
}) {
  const { events, connected, lastEventAt, done } = useRunEvents(initial.run_id);

  const status = latestRunStatus(events, initial.status);
  const rehearsal = isRehearsal(events);
  const budget = latestBudget(events, initial.budget ?? null);
  const landReport = latestLandReport(
    events,
    (initial.land_report as Record<string, unknown> | null) ?? null,
  );
  const plan = latestPlan(events, initial.plan ?? null);
  const board = useMemo(
    () => planBoardSteps(plan, initial.steps ?? [], stepStatuses(events)),
    [plan, initial.steps, events],
  );
  const children = useMemo(() => childRuns(events), [events]);
  // Cheap pure scan; recomputing per render keeps the compiler's memo intact.
  const blockedSteps = openBlockedSteps(events, initial);
  const kind = heldKind(status);
  const version = planVersion(plan);
  const workspaceId = workspaceIdFrom(events);
  const deliverables = landReport?.["deliverables"];
  const filesCount = Array.isArray(deliverables) ? deliverables.length : null;

  // Child events compact into one group block anchored at the first spawn.
  const anchorSeq = children.length > 0 ? children[0]!.spawnedSeq : null;
  const cardClass = [
    "rounded-lg border bg-card p-5",
    rehearsal ? "border-dashed border-graphite" : "border-line",
  ].join(" ");

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-5">
      {rehearsal && <RehearsalBanner exitHref="/app/runs" />}
      {!connected && !done && <DisconnectBanner lastEventAt={lastEventAt ?? undefined} />}

      <header className="flex flex-wrap items-center gap-3">
        <h1 className="font-display text-2xl text-ink">{initial.goal}</h1>
        <StatusChip status={status} rehearsal={rehearsal} />
      </header>
      {kind && (
        <p role="status" className="rounded-md border border-hold-soft bg-hold-soft px-4 py-2 text-sm text-hold">
          {checkpointKinds[kind].description}
        </p>
      )}

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_280px]">
        <div className="flex min-w-0 flex-col gap-5">
          <section aria-label="Plan" className={cardClass}>
            <h2 className="mb-4 font-display text-lg text-ink">Plan</h2>
            {board.length > 0 ? (
              <RouteStepList
                steps={board.map((step) => ({
                  id: step.id,
                  sentence: step.sentence,
                  state: routeStateFor(step.status),
                  checkpoint: step.checkpoint,
                }))}
              />
            ) : (
              <p className="text-sm text-muted">The plan appears here once it is drafted.</p>
            )}
          </section>

          <section aria-label="Timeline" className={cardClass}>
            <h2 className="mb-2 font-display text-lg text-ink">Timeline</h2>
            {events.length === 0 ? (
              <p className="text-sm text-muted">Waiting for the first update.</p>
            ) : (
              <ol className="m-0 list-none p-0">
                {events.map((event) => {
                  if (event.type === "child_spawned" || event.type === "child_landed") {
                    return event.seq === anchorSeq ? (
                      <FanoutGroup key="fanout">{children}</FanoutGroup>
                    ) : null;
                  }
                  // A scripted answer is stamped with the clock's position
                  // after the advance that delivered it; the moment the
                  // simulated human answered is the honest primary stamp.
                  const simulatedAt = event.payload["simulated_at"];
                  const virtualAt =
                    event.type === "gate_answered" && typeof simulatedAt === "string"
                      ? simulatedAt
                      : (event.virtual_ts ?? undefined);
                  return (
                    <TimelineRow
                      key={event.seq}
                      event={event.type}
                      actor={event.actor}
                      at={event.ts}
                      virtualAt={virtualAt}
                      payload={event.payload}
                    />
                  );
                })}
              </ol>
            )}
          </section>

          {(viewer?.canAct ?? true) && (
            <RunControls
              runId={initial.run_id}
              status={status}
              rehearsal={rehearsal}
              virtualNow={latestVirtualTs(events)}
              blockedSteps={blockedSteps}
              planVersion={version}
              planSteps={board.map((step) => step.sentence)}
              events={events}
              commands={commands}
            />
          )}

          {landReport && (
            <LandReportSection
              report={landReport}
              budget={budget}
              steps={board.map((step) => ({
                step_id: step.id,
                description: step.sentence,
                status: step.status,
              }))}
              rehearsal={rehearsal}
            >
              <div className="flex flex-wrap items-center gap-4">
                {(viewer?.canExportEvidence ?? true) && (
                  <a
                    href={`/api/runs/${initial.run_id}/evidence`}
                    download
                    className="inline-flex items-center rounded-md border border-line bg-card px-3 py-1.5 text-sm font-medium text-ink hover:bg-field"
                  >
                    {copy.exportEvidenceBinder}
                  </a>
                )}
                {rehearsal && (viewer?.canSubmitPromotion ?? false) && (
                  <PromotionBlock
                    runId={initial.run_id}
                    promotion={promotion}
                    submitPromotion={submitPromotion}
                  />
                )}
              </div>
            </LandReportSection>
          )}
        </div>

        <aside className="flex flex-col gap-5">
          <section aria-label="Vitals" className={cardClass}>
            <dl className="space-y-4 text-sm">
              <div>
                <dt className="text-muted">Status</dt>
                <dd className="mt-1">
                  <StatusChip status={status} rehearsal={rehearsal} />
                </dd>
              </div>
              {budget && (
                <div>
                  <dt className="text-muted">Budget</dt>
                  <dd className="mt-1">
                    <BudgetMeter budget={budget} />
                  </dd>
                </div>
              )}
              <div>
                <dt className="text-muted">Files</dt>
                <dd className="mt-1 font-mono text-xs text-ink">
                  {filesCount !== null ? filesCount : <span className="text-muted">None yet</span>}
                </dd>
              </div>
            </dl>

            <details className="mt-5 border-t border-line-soft pt-3">
              <summary className="cursor-pointer text-xs text-muted">{copy.operatorDetails}</summary>
              <dl className="mt-2 space-y-2 text-xs">
                <div>
                  <dt className="text-muted">Run id</dt>
                  <dd className="mt-0.5 flex items-center gap-2">
                    <span className="font-mono text-ink">{initial.run_id}</span>
                    <button
                      type="button"
                      className="rounded border border-line px-1.5 py-0.5 text-muted hover:text-ink"
                      onClick={() => void navigator.clipboard.writeText(initial.run_id)}
                    >
                      {copy.copyRunId}
                    </button>
                  </dd>
                </div>
                {workspaceId && (
                  <div>
                    <dt className="text-muted">Workspace</dt>
                    <dd className="mt-0.5 font-mono text-ink">{workspaceId}</dd>
                  </div>
                )}
                {version !== null && (
                  <div>
                    <dt className="text-muted">Plan</dt>
                    <dd className="mt-0.5 font-mono text-ink">{copy.pinnedVersion(version)}</dd>
                  </div>
                )}
                {events.length > 0 && (
                  <div>
                    <dt className="text-muted">Last event</dt>
                    {/* ISO stays on hover; the stamp itself reads friendly. */}
                    <dd className="mt-0.5 font-mono text-ink" title={events[events.length - 1]!.ts}>
                      {friendlyDateTime(events[events.length - 1]!.ts)}
                    </dd>
                  </div>
                )}
              </dl>
            </details>
          </section>
        </aside>
      </div>
    </div>
  );
}
