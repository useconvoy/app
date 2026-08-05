/**
 * Pure derivations over run data: held-kind mapping, list filter groups,
 * progress, and reductions over the live event stream. Shared by server
 * pages and client components; no IO, no server-only imports.
 *
 * Domain shapes stay with the generated client (`RunView` and friends).
 * `RunStreamEvent` below is not a domain type: it is the SSE envelope the
 * proxy pipes through byte-for-byte, which the control plane's OpenAPI
 * declares as an untyped stream. Payloads remain `unknown`-shaped and are
 * read defensively; the recorded fixtures in tests/fixtures/sse pin the
 * real shapes in the test suites.
 */
import type { BudgetView, RunView } from "@/lib/api/client";

export interface RunStreamEvent {
  id: string;
  run_id: string;
  tenant_id: string;
  seq: number;
  type: string;
  ts: string;
  virtual_ts: string | null;
  sandbox: boolean;
  actor: string;
  actor_type: string;
  payload: Record<string, unknown>;
}

/** The three stuck states, each with its own typed verb. */
export type HeldKind = "paused" | "awaiting_approval" | "blocked_on_human";

const HELD_KINDS: readonly HeldKind[] = ["paused", "awaiting_approval", "blocked_on_human"];

export function heldKind(status: string): HeldKind | null {
  return (HELD_KINDS as readonly string[]).includes(status) ? (status as HeldKind) : null;
}

/** Filter chips on the runs list: All plus these four groups. */
export type RunFilterGroup = "held" | "running" | "failed" | "landed";

export function filterGroup(status: string): RunFilterGroup | null {
  if (heldKind(status)) return "held";
  switch (status) {
    case "planning":
    case "running":
    case "landing":
      return "running";
    case "failed":
    case "budget_exhausted":
      return "failed";
    case "completed":
    case "landed":
      return "landed";
    default:
      return null;
  }
}

/** Done steps over total, for the list's progress column. */
export function progress(steps: ReadonlyArray<{ status: string }>): { done: number; total: number } {
  return {
    done: steps.filter((step) => step.status === "done").length,
    total: steps.length,
  };
}

/** Latest run status carried by the stream, falling back to the fetched view. */
export function latestRunStatus(events: readonly RunStreamEvent[], fallback: string): string {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const status = events[i]!.payload["run_status"];
    if (typeof status === "string") return status;
  }
  return fallback;
}

/** Latest budget carried by the stream (step/budget/child events all carry one). */
export function latestBudget(
  events: readonly RunStreamEvent[],
  fallback: BudgetView | null,
): BudgetView | null {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const budget = events[i]!.payload["budget"];
    if (budget && typeof budget === "object") return budget as BudgetView;
  }
  return fallback;
}

/** The land report once `run_completed` carries one. */
export function latestLandReport(
  events: readonly RunStreamEvent[],
  fallback: Record<string, unknown> | null,
): Record<string, unknown> | null {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const report = events[i]!.payload["land_report"];
    if (report && typeof report === "object") return report as Record<string, unknown>;
  }
  return fallback;
}

/** Rehearsal context comes from the stream itself, never from the URL or props. */
export function isRehearsal(events: readonly RunStreamEvent[], fallback = false): boolean {
  return events.length > 0 ? events[0]!.sandbox === true : fallback;
}

/** Per-step live status derived from the event narrative. */
export function stepStatuses(events: readonly RunStreamEvent[]): Map<string, string> {
  const statuses = new Map<string, string>();
  for (const event of events) {
    const stepId = event.payload["step_id"];
    if (typeof stepId !== "string") continue;
    switch (event.type) {
      case "step_started":
        statuses.set(stepId, "running");
        break;
      case "step_done":
        statuses.set(stepId, "done");
        break;
      case "step_failed":
        statuses.set(stepId, "failed");
        break;
      case "step_skipped":
        statuses.set(stepId, "skipped");
        break;
      case "gate_opened":
        statuses.set(stepId, "blocked_on_human");
        break;
      case "gate_answered":
        statuses.set(stepId, "running");
        break;
      default:
        break;
    }
  }
  return statuses;
}

/** The latest plan dict carried by the stream, falling back to the view's. */
export function latestPlan(
  events: readonly RunStreamEvent[],
  fallback: RunView["plan"],
): RunView["plan"] {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const event = events[i]!;
    if (event.type !== "plan_created" && event.type !== "revision_applied") continue;
    const plan = event.payload["plan"];
    if (plan && typeof plan === "object") return plan as RunView["plan"];
  }
  return fallback;
}

export interface PlanBoardStep {
  id: string;
  sentence: string;
  status: string;
  /** A human gate sits on this step: render the checkpoint marker. */
  checkpoint: boolean;
}

/**
 * Plan board rows: the plan dict's steps (which know about gates) overlaid
 * with live per-step statuses from the stream. Falls back to the view's
 * flat steps when no plan has arrived yet.
 */
export function planBoardSteps(
  plan: RunView["plan"],
  viewSteps: RunView["steps"],
  live: Map<string, string>,
): PlanBoardStep[] {
  const planSteps = Array.isArray((plan as Record<string, unknown> | null)?.["steps"])
    ? ((plan as Record<string, unknown>)["steps"] as Array<Record<string, unknown>>)
    : null;
  // The plan dict snapshot goes stale as steps execute; the fetched view's
  // flat steps carry the projection's current statuses as a second source.
  const viewStatus = new Map(viewSteps.map((step) => [step.step_id, step.status]));
  if (planSteps) {
    return planSteps.map((step) => {
      const id = typeof step["id"] === "string" ? (step["id"] as string) : "";
      const status =
        live.get(id) ??
        viewStatus.get(id) ??
        (typeof step["status"] === "string" ? (step["status"] as string) : "pending");
      return {
        id,
        sentence: typeof step["description"] === "string" ? (step["description"] as string) : "",
        status,
        checkpoint: step["human_gate"] != null || status === "blocked_on_human",
      };
    });
  }
  return viewSteps.map((step) => ({
    id: step.step_id,
    sentence: step.description ?? "",
    status: live.get(step.step_id) ?? step.status,
    checkpoint: (live.get(step.step_id) ?? step.status) === "blocked_on_human",
  }));
}

/** Plan board status -> route motif node state. */
export function routeStateFor(status: string): "done" | "active" | "held" | "queued" | "failed" {
  switch (status) {
    case "done":
      return "done";
    case "running":
      return "active";
    case "blocked_on_human":
      return "held";
    case "failed":
      return "failed";
    default:
      return "queued";
  }
}

export interface ChildRunLine {
  childRunId: string;
  groupId: string | null;
  /** Plain sentence for the group line: the landed headline, else the goal. */
  headline: string;
  costUsd: string | null;
  /** step status vocabulary: running until landed, then done/failed. */
  status: string;
  /** seq of the child_spawned event, to anchor the group in the timeline. */
  spawnedSeq: number;
}

/**
 * Fan-out compaction: child_spawned/child_landed pairs merge into one
 * line per child, grouped under the parent's group step.
 */
export function childRuns(events: readonly RunStreamEvent[]): ChildRunLine[] {
  const children = new Map<string, ChildRunLine>();
  for (const event of events) {
    const childRunId = event.payload["child_run_id"];
    if (typeof childRunId !== "string") continue;
    if (event.type === "child_spawned") {
      children.set(childRunId, {
        childRunId,
        groupId: typeof event.payload["group_id"] === "string" ? (event.payload["group_id"] as string) : null,
        headline: typeof event.payload["goal"] === "string" ? (event.payload["goal"] as string) : childRunId,
        costUsd: null,
        status: "running",
        spawnedSeq: event.seq,
      });
    } else if (event.type === "child_landed") {
      const existing = children.get(childRunId);
      if (!existing) continue;
      const headline = event.payload["headline"];
      const cost = event.payload["cost_usd"];
      const status = event.payload["status"];
      children.set(childRunId, {
        ...existing,
        headline: typeof headline === "string" ? headline : existing.headline,
        costUsd: typeof cost === "string" ? cost : existing.costUsd,
        status: typeof status === "string" ? status : existing.status,
      });
    }
  }
  return [...children.values()].sort((a, b) => a.spawnedSeq - b.spawnedSeq);
}
