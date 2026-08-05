/**
 * Run listing and creation over the control plane.
 *
 * The runtime does not yet expose an org-wide run list; that addition is
 * requested as runtime D8 (`GET /runs`). Until it lands, this directory
 * tracks run ids per tenant in process memory: runs created through the
 * console register here, and every read hydrates through the real
 * `GET /runs/{id}` so nothing domain-shaped is ever cached or persisted on
 * the website side. TODO(runtime-D8): replace the registry with the
 * runtime's list endpoint and delete the registration seam.
 */
import "server-only";

import { controlPlane, type ActorContext, type RunView } from "./client";

/** Console-side facts recorded at creation time, keyed by run id. */
interface RunRecord {
  runId: string;
  tenantId: string;
  /** Routine the run was triggered from; drives assignment routing. */
  routineId?: string;
  createdAt: string;
}

declare global {
  var __convoyRunDirectory: Map<string, RunRecord> | undefined;
}

function directory(): Map<string, RunRecord> {
  if (!globalThis.__convoyRunDirectory) {
    globalThis.__convoyRunDirectory = new Map();
  }
  return globalThis.__convoyRunDirectory;
}

export function registerRun(record: Omit<RunRecord, "createdAt">): void {
  directory().set(record.runId, { ...record, createdAt: new Date().toISOString() });
}

/**
 * Register a run the console first met through its detail page (created
 * outside the console, e.g. by an operator at the edge), so lists and the
 * Checkpoints inbox can see it. Never overwrites a creation-time record,
 * which knows the routine. TODO(runtime-D8): retired with the directory.
 */
export function ensureRunRegistered(runId: string, tenantId: string): void {
  if (!directory().has(runId)) {
    directory().set(runId, { runId, tenantId, createdAt: new Date().toISOString() });
  }
}

export function routineIdForRun(runId: string): string | undefined {
  return directory().get(runId)?.routineId;
}

export function knownRunIds(tenantId: string): string[] {
  return [...directory().values()]
    .filter((record) => record.tenantId === tenantId)
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt))
    .map((record) => record.runId);
}

/**
 * Hydrate every known run for the tenant through the single authenticated
 * edge. Runs the control plane no longer knows (404) drop out silently; a
 * 404 is a 404 and is never probed further.
 */
export async function listRuns(actor: ActorContext): Promise<RunView[]> {
  const client = controlPlane(actor);
  const views = await Promise.all(
    knownRunIds(actor.tenantId).map(async (runId) => {
      const { data } = await client.GET("/runs/{run_id}", {
        params: { path: { run_id: runId } },
      });
      return data ?? null;
    }),
  );
  return views.filter((view): view is RunView => view !== null);
}

export async function getRun(actor: ActorContext, runId: string): Promise<RunView | null> {
  const { data } = await controlPlane(actor).GET("/runs/{run_id}", {
    params: { path: { run_id: runId } },
  });
  return data ?? null;
}

export interface CreateRunInput {
  goal: string;
  environmentId: string;
  budgetUsd: string;
  routineId?: string;
  requirePlanApproval?: boolean;
}

/** Create a run at the edge and register it so the list can see it. */
export async function createRun(
  actor: ActorContext,
  input: CreateRunInput,
): Promise<{ runId: string; status: string }> {
  const { data, error } = await controlPlane(actor).POST("/runs", {
    // The generated body type surfaces the API's defaulted fields as
    // required; they are sent verbatim at their documented defaults.
    body: {
      goal: input.goal,
      environment_id: input.environmentId,
      budget_usd: input.budgetUsd,
      fixture_gates: {},
      max_children: 5,
      success_criteria: [],
      tools: [],
      ...(input.requirePlanApproval === undefined
        ? {}
        : {
            policy: {
              approval_scope: "major_revisions" as const,
              max_depth: 1,
              max_parallel: 5,
              max_steps: 50,
              on_budget_exhausted: "pause" as const,
              on_group_partial_failure: "join_with_partials" as const,
              plan_shape: "linear_fanout" as const,
              require_plan_approval: input.requirePlanApproval,
            },
          }),
    },
  });
  if (error || !data) {
    throw new Error("run creation was not accepted");
  }
  registerRun({ runId: data.run_id, tenantId: actor.tenantId, routineId: input.routineId });
  return { runId: data.run_id, status: data.status };
}
