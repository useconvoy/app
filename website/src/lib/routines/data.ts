/**
 * Routine view assembly: the org's stored routine definitions joined with
 * real run history from the control plane's run list, approver assignments
 * from the website DB, and workspace grants from the environments adapter.
 * Everything a routine surface renders comes through here so the data
 * seams stay in one place. A fresh organization has no routines and every
 * consumer renders a designed empty state.
 */
import "server-only";

import type { ActorContext } from "@/lib/api/client";
import {
  environmentsClient,
  type Agent,
  type SystemGrant,
  type Workspace,
} from "@/lib/api/environments";
import { isRehearsalTarget, listRuns } from "@/lib/api/runs";
import { selectWorkspace, type WorkspaceSelection } from "@/lib/workspaces/fit";
import {
  getRoutine,
  listApprovers,
  type ApproverAssignment,
  type RoutineRecord,
} from "./queries";
import { triggersForRoutine, type RoutineTriggers } from "./triggers";

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
    const routineId = view.agent_id;
    if (!routineId) continue;
    const summaries = byRoutine.get(routineId) ?? [];
    summaries.push({
      runId: view.run_id,
      status: view.status,
      spentUsd: view.budget?.spent_usd ?? null,
      rehearsal: isRehearsalTarget(view.environment_id),
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
  /** The selected agent and every agent available for reassignment. */
  agent: Agent | null;
  agents: Agent[];
  /** The workspace runs use, after the precedence in resolveWorkspaceSelection. */
  workspace: Workspace | null;
  /**
   * Set when the routine's recorded workspace no longer covers its
   * systems; the detail page renders a plain warning from it while runs
   * fall back to the auto-match in `workspace`.
   */
  staleAssignment: { workspace: Workspace; missingSystems: string[] } | null;
  /** Every workspace of the org, for the reassignment picker. */
  workspaces: Workspace[];
  /** Workspace grants for the systems this routine uses. */
  systems: SystemGrant[];
  approvers: ApproverAssignment[];
  triggers: RoutineTriggers;
  runs: RoutineRunSummary[];
}

/**
 * Resolve a routine's workspace with full detail. Precedence (the pure
 * rule lives in lib/workspaces/fit selectWorkspace): the recorded binding
 * wins while it exists and still covers the routine's systems; otherwise
 * the routine falls back to the first workspace covering them, and a
 * stale recorded binding is reported so surfaces can warn about it.
 */
export async function resolveWorkspaceSelection(
  orgId: string,
  routine: RoutineRecord,
): Promise<WorkspaceSelection<Workspace>> {
  const workspaces = await environmentsClient().listWorkspaces(orgId);
  return selectWorkspace(routine, workspaces);
}

/** The workspace a run of this routine would use, or null when none fits. */
export async function resolveWorkspace(
  orgId: string,
  routine: RoutineRecord,
): Promise<Workspace | null> {
  return (await resolveWorkspaceSelection(orgId, routine)).workspace;
}

export async function getRoutineDetail(
  orgId: string,
  actor: ActorContext,
  routineId: string,
): Promise<RoutineDetailData | null> {
  const routine = await getRoutine(orgId, routineId);
  if (!routine) return null;
  const [workspaces, agents, approvers, history] = await Promise.all([
    environmentsClient().listWorkspaces(orgId),
    environmentsClient().listAgents(orgId),
    listApprovers(orgId, routineId),
    runHistory(actor),
  ]);
  const { workspace, staleAssignment } = selectWorkspace(routine, workspaces);
  const grants = new Map((workspace?.systems ?? []).map((grant) => [grant.systemId, grant]));
  return {
    routine,
    agent: routine.agentId ? agents.find((agent) => agent.id === routine.agentId) ?? null : null,
    agents,
    workspace,
    staleAssignment,
    workspaces,
    systems: routine.systems
      .map((systemId) => grants.get(systemId))
      .filter((grant): grant is SystemGrant => grant !== undefined),
    approvers,
    triggers: triggersForRoutine(routine),
    runs: history.get(routineId) ?? [],
  };
}
