/**
 * Routine view assembly: fixture routine definitions (the world) joined
 * with real run history from the run directory, approver assignments from
 * the website DB, workspace grants from the environments fixture adapter,
 * and trigger configuration. Everything a routine surface renders comes
 * through here so the fixture seams stay in one place.
 */
import "server-only";

import type { ActorContext } from "@/lib/api/client";
import {
  environmentsClient,
  workspaceForRoutine,
  type SystemGrant,
  type Workspace,
} from "@/lib/api/environments";
import { listRuns, routineIdForRun } from "@/lib/api/runs";
import { routines, type FixtureRoutine } from "@/lib/fixtures/world";
import { listApprovers, type ApproverAssignment } from "./queries";
import { getTriggers, type RoutineTriggers } from "./triggers";

/**
 * Which lens an on-demand run was started under. RunView carries no
 * rehearsal flag, so the console remembers what it asked for; production
 * stays the unlabeled default for anything it did not start.
 * TODO(runtime-D8): the run list endpoint's sandbox flag replaces this.
 */
declare global {
  var __convoyRunTargets: Map<string, "rehearsal" | "production"> | undefined;
}

function runTargets(): Map<string, "rehearsal" | "production"> {
  if (!globalThis.__convoyRunTargets) {
    globalThis.__convoyRunTargets = new Map();
  }
  return globalThis.__convoyRunTargets;
}

export function recordRunTarget(runId: string, target: "rehearsal" | "production"): void {
  runTargets().set(runId, target);
}

export function isRehearsalRun(runId: string): boolean {
  return runTargets().get(runId) === "rehearsal";
}

export interface RoutineRunSummary {
  runId: string;
  status: string;
  spentUsd: string | null;
  rehearsal: boolean;
}

/**
 * Real run history for one routine, newest first. The control plane may
 * be unreachable in fixture-only setups; the surfaces stay up with an
 * empty history rather than failing the whole page.
 */
async function runHistory(actor: ActorContext): Promise<Map<string, RoutineRunSummary[]>> {
  const byRoutine = new Map<string, RoutineRunSummary[]>();
  let views;
  try {
    views = await listRuns(actor);
  } catch {
    return byRoutine;
  }
  for (const view of views) {
    const routineId = routineIdForRun(view.run_id);
    if (!routineId) continue;
    const summaries = byRoutine.get(routineId) ?? [];
    summaries.push({
      runId: view.run_id,
      status: view.status,
      spentUsd: view.budget?.spent_usd ?? null,
      rehearsal: isRehearsalRun(view.run_id),
    });
    byRoutine.set(routineId, summaries);
  }
  return byRoutine;
}

export interface RoutineListItem {
  id: string;
  name: string;
  descriptor: string;
  systemCount: number;
  /** Latest run's status, or null when the routine has never run. */
  latestRunStatus: string | null;
  latestRunRehearsal: boolean;
}

/** The routines list: plain descriptors with health from the latest run. */
export async function listRoutineViews(actor: ActorContext): Promise<RoutineListItem[]> {
  const history = await runHistory(actor);
  return routines.map((routine) => {
    const latest = history.get(routine.id)?.[0];
    return {
      id: routine.id,
      name: routine.name,
      descriptor: routine.descriptor,
      systemCount: routine.systems.length,
      latestRunStatus: latest?.status ?? null,
      latestRunRehearsal: latest?.rehearsal ?? false,
    };
  });
}

/**
 * Checkpoint placements per routine, as plain sentences. Fixture-side for
 * now: placements live in the plan template the runtime owns.
 */
const CHECKPOINT_PLACEMENTS: Record<string, Array<{ title: string; behavior: string }>> = {
  "routine-access-review": [
    {
      title: "Exception memos wait for approval before anyone is chased",
      behavior: "If no one answers in time, the run pauses.",
    },
  ],
  "routine-vendor-check": [
    {
      title: "Document requests wait for approval before they go to vendors",
      behavior: "If no one answers in time, the run pauses.",
    },
  ],
  "routine-attestation-chase": [
    {
      title: "The reminder list waits for a look before reminders go out",
      behavior: "If no one answers in time, this step is skipped.",
    },
  ],
};

export interface RoutineDetailData {
  routine: FixtureRoutine;
  workspace: Workspace | null;
  /** Workspace grants for the systems this routine uses. */
  systems: SystemGrant[];
  checkpoints: Array<{ title: string; behavior: string }>;
  approvers: ApproverAssignment[];
  triggers: RoutineTriggers;
  runs: RoutineRunSummary[];
}

export async function getRoutineDetail(
  orgId: string,
  actor: ActorContext,
  routineId: string,
): Promise<RoutineDetailData | null> {
  const routine = routines.find((candidate) => candidate.id === routineId);
  if (!routine) return null;
  const [workspaces, approvers, history] = await Promise.all([
    environmentsClient().listWorkspaces(orgId),
    listApprovers(orgId, routineId),
    runHistory(actor),
  ]);
  const workspace = workspaceForRoutine(routine, workspaces);
  const grants = new Map((workspace?.systems ?? []).map((grant) => [grant.systemId, grant]));
  return {
    routine,
    workspace,
    systems: routine.systems
      .map((systemId) => grants.get(systemId))
      .filter((grant): grant is SystemGrant => grant !== undefined),
    checkpoints: CHECKPOINT_PLACEMENTS[routineId] ?? [],
    approvers,
    triggers: getTriggers(orgId, routineId),
    runs: history.get(routineId) ?? [],
  };
}
