/**
 * Routine view assembly: the org's stored routine definitions joined with
 * real run history from the run directory, approver assignments from the
 * website DB, and workspace grants from the environments adapter.
 * Everything a routine surface renders comes through here so the data
 * seams stay in one place. A fresh organization has no routines and every
 * consumer renders a designed empty state.
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
import {
  getRoutine,
  listApprovers,
  type ApproverAssignment,
  type RoutineRecord,
} from "./queries";
import { triggersForRoutine, type RoutineTriggers } from "./triggers";

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
 * be unreachable; the surfaces stay up with an empty history rather than
 * failing the whole page.
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
export async function listRoutineViews(
  routines: RoutineRecord[],
  actor: ActorContext,
): Promise<RoutineListItem[]> {
  const history = routines.length > 0 ? await runHistory(actor) : new Map<string, RoutineRunSummary[]>();
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

export interface RoutineDetailData {
  routine: RoutineRecord;
  workspace: Workspace | null;
  /** Workspace grants for the systems this routine uses. */
  systems: SystemGrant[];
  approvers: ApproverAssignment[];
  triggers: RoutineTriggers;
  runs: RoutineRunSummary[];
}

/**
 * Resolve a routine's workspace: the recorded binding wins; a routine
 * without one falls back to the first workspace covering its systems.
 */
export async function resolveWorkspace(
  orgId: string,
  routine: RoutineRecord,
): Promise<Workspace | null> {
  const client = environmentsClient();
  if (routine.workspaceId) {
    return client.getWorkspace(orgId, routine.workspaceId);
  }
  const workspaces = await client.listWorkspaces(orgId);
  return workspaceForRoutine(routine, workspaces);
}

export async function getRoutineDetail(
  orgId: string,
  actor: ActorContext,
  routineId: string,
): Promise<RoutineDetailData | null> {
  const routine = await getRoutine(orgId, routineId);
  if (!routine) return null;
  const [workspace, approvers, history] = await Promise.all([
    resolveWorkspace(orgId, routine),
    listApprovers(orgId, routineId),
    runHistory(actor),
  ]);
  const grants = new Map((workspace?.systems ?? []).map((grant) => [grant.systemId, grant]));
  return {
    routine,
    workspace,
    systems: routine.systems
      .map((systemId) => grants.get(systemId))
      .filter((grant): grant is SystemGrant => grant !== undefined),
    approvers,
    triggers: triggersForRoutine(routine),
    runs: history.get(routineId) ?? [],
  };
}
