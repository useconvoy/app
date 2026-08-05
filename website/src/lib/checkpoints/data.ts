/**
 * Server-side assembly of the Checkpoints inbox: the three stuck kinds
 * from the live run list, each with its one typed verb,
 * plus promotion reviews. Deadlines join in from the website-side
 * open_gates rows the notifier keeps; team-held labeling and assignee
 * chips come from routine assignments. Nothing domain-shaped is stored:
 * runs are read through the edge on every request.
 */
import "server-only";

import type { TimeoutBehavior } from "@/lexicon";
import { checkpointKinds, notificationTitles } from "@/lexicon";
import type { ActorContext, RunView } from "@/lib/api/client";
import { listRuns, routineIdForRun } from "@/lib/api/runs";
import { withOrgContext } from "@/lib/db";
import { minutesSince } from "@/lib/format";
import { routines } from "@/lib/fixtures/world";
import { listPromotionRequests } from "@/lib/promotions/store";
import { isRehearsalRun } from "@/lib/routines/data";
import { listApprovers, type ApproverAssignment } from "@/lib/routines/queries";
import { heldKind } from "@/lib/runs/status";

export type CheckpointItemKind =
  | "paused"
  | "awaiting_approval"
  | "blocked_on_human"
  | "promotion";

export interface CheckpointItemAssignee {
  name: string;
  kind: "person" | "team";
}

export interface CheckpointItem {
  /** Stable key for list rendering and per-item UI state. */
  key: string;
  kind: CheckpointItemKind;
  runId: string;
  /** What is being asked, as a plain sentence. */
  prompt: string;
  /** Which run this belongs to, in plain language. */
  runContext: string;
  heldMinutes: number;
  /** ISO deadline from open_gates or the gate itself; null when open-ended. */
  deadline: string | null;
  onTimeout: TimeoutBehavior | null;
  /** Approve items carry the version the card's action will submit. */
  planVersion: number | null;
  /** Respond items carry the held step. */
  stepId: string | null;
  rehearsal: boolean;
  teamHeld: boolean;
  assignees: CheckpointItemAssignee[];
  /** Promotion items link through to their review page. */
  promotionRequestId: string | null;
}

interface PlanStepGate {
  prompt: string | null;
  onTimeout: TimeoutBehavior | null;
}

/** Checkpoint facts per step id, read defensively from the plan dict. */
function planHolds(plan: RunView["plan"]): Map<string, PlanStepGate> {
  const holds = new Map<string, PlanStepGate>();
  const steps = (plan as Record<string, unknown> | null)?.["steps"];
  if (!Array.isArray(steps)) return holds;
  for (const raw of steps as Array<Record<string, unknown>>) {
    const id = typeof raw["id"] === "string" ? (raw["id"] as string) : null;
    const hold = raw["human_gate"];
    if (!id || !hold || typeof hold !== "object") continue;
    const prompt = (hold as Record<string, unknown>)["prompt"];
    const onTimeout = (hold as Record<string, unknown>)["on_timeout"];
    holds.set(id, {
      prompt: typeof prompt === "string" ? prompt : null,
      onTimeout:
        onTimeout === "pause" || onTimeout === "skip" || onTimeout === "fail"
          ? onTimeout
          : null,
    });
  }
  return holds;
}

function newestStepUpdate(run: RunView): string | null {
  let newest: string | null = null;
  for (const step of run.steps ?? []) {
    const at = step.updated_at ?? null;
    if (at && (!newest || at > newest)) newest = at;
  }
  return newest;
}

/** Deadlines the notifier tracked from gate_opened, keyed run:step. */
async function openGateDeadlines(
  orgId: string,
  userId: string,
): Promise<Map<string, string | null>> {
  return withOrgContext({ orgId, userId }, async (client) => {
    const { rows } = await client.query<{ runId: string; stepId: string; deadline: Date | null }>(
      `SELECT run_id AS "runId", step_id AS "stepId", deadline
         FROM open_gates WHERE org_id = $1`,
      [orgId],
    );
    return new Map(
      rows.map((row) => [
        `${row.runId}:${row.stepId}`,
        row.deadline ? row.deadline.toISOString() : null,
      ]),
    );
  });
}

/** The full inbox for one org, deadline-first then longest-held. */
export async function gatherCheckpointItems(
  orgId: string,
  userId: string,
  actor: ActorContext,
): Promise<CheckpointItem[]> {
  let runs: RunView[] = [];
  try {
    runs = await listRuns(actor);
  } catch {
    // The control plane may be unreachable; the inbox stays calm.
    runs = [];
  }
  let deadlines = new Map<string, string | null>();
  try {
    deadlines = await openGateDeadlines(orgId, userId);
  } catch {
    deadlines = new Map();
  }

  // Assignee chips are per routine; cache lookups across items.
  const approverCache = new Map<string, ApproverAssignment[]>();
  async function approversFor(routineId: string | undefined): Promise<ApproverAssignment[]> {
    if (!routineId) return [];
    const cached = approverCache.get(routineId);
    if (cached) return cached;
    let assignments: ApproverAssignment[] = [];
    try {
      assignments = await listApprovers(orgId, routineId);
    } catch {
      assignments = [];
    }
    approverCache.set(routineId, assignments);
    return assignments;
  }

  const items: CheckpointItem[] = [];
  for (const run of runs) {
    const kind = heldKind(run.status);
    if (!kind) continue;
    const routineId = routineIdForRun(run.run_id);
    const approvers = await approversFor(routineId);
    const assignees: CheckpointItemAssignee[] = approvers.map((assignment) => ({
      name: assignment.name,
      kind: assignment.assigneeType === "team" ? "team" : "person",
    }));
    const base = {
      runId: run.run_id,
      runContext: run.goal,
      rehearsal: isRehearsalRun(run.run_id),
      teamHeld: assignees.some((assignee) => assignee.kind === "team"),
      assignees,
      promotionRequestId: null,
    };
    const heldSince = newestStepUpdate(run);
    const heldMinutes = heldSince ? minutesSince(heldSince) : 0;

    if (kind === "paused") {
      items.push({
        ...base,
        key: `paused:${run.run_id}`,
        kind,
        prompt: checkpointKinds.paused.description,
        heldMinutes,
        deadline: null,
        onTimeout: null,
        planVersion: null,
        stepId: null,
      });
    } else if (kind === "awaiting_approval") {
      const version = (run.plan as Record<string, unknown> | null)?.["version"];
      items.push({
        ...base,
        key: `approve:${run.run_id}`,
        kind,
        prompt: notificationTitles.approve(run.goal),
        heldMinutes,
        deadline: null,
        onTimeout: null,
        planVersion: typeof version === "number" ? version : 1,
        stepId: null,
      });
    } else {
      const holds = planHolds(run.plan ?? null);
      for (const step of run.steps ?? []) {
        if (step.status !== "blocked_on_human") continue;
        const hold = holds.get(step.step_id);
        items.push({
          ...base,
          key: `respond:${run.run_id}:${step.step_id}`,
          kind,
          prompt: hold?.prompt ?? step.description ?? checkpointKinds.blocked_on_human.description,
          heldMinutes: step.updated_at ? minutesSince(step.updated_at) : heldMinutes,
          deadline: deadlines.get(`${run.run_id}:${step.step_id}`) ?? null,
          onTimeout: hold?.onTimeout ?? null,
          planVersion: null,
          stepId: step.step_id,
        });
      }
    }
  }

  // Promotion reviews: rehearsal results waiting before anything goes live.
  for (const request of listPromotionRequests(orgId)) {
    if (request.status !== "requested") continue;
    const routine = routines.find((candidate) => candidate.id === request.routineId);
    items.push({
      key: `promotion:${request.id}`,
      kind: "promotion",
      runId: request.runId,
      prompt: checkpointKinds.promotion.description,
      runContext: routine?.name ?? request.runId,
      heldMinutes: minutesSince(request.requestedAt),
      deadline: null,
      onTimeout: null,
      planVersion: null,
      stepId: null,
      rehearsal: true,
      teamHeld: false,
      assignees: [],
      promotionRequestId: request.id,
    });
  }

  return items.sort((a, b) => {
    if (a.deadline && b.deadline) return a.deadline.localeCompare(b.deadline);
    if (a.deadline) return -1;
    if (b.deadline) return 1;
    return b.heldMinutes - a.heldMinutes;
  });
}
