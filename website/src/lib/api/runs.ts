/**
 * Run listing and creation over the control plane.
 *
 * `GET /runs` is the run directory now: it serves the tenant's runs newest
 * first with console attribution (agent_id, started_by, started_via) and the
 * exact binding target (environment_id) on every row. Nothing about runs is
 * remembered website-side anymore — the old in-process registry and the
 * rehearsal-target map are gone, so runs started by any process (seed
 * scripts, schedules, other instances) are first-class in every surface.
 */
import "server-only";

import { controlPlane, type ActorContext, type RunView } from "./client";
import type { components } from "./schema";

export type RunSummary = components["schemas"]["RunSummary"];

export interface ListRunsOptions {
  status?: string;
  /** Narrow to runs launched from one Agent. */
  agentId?: string;
  /** Keyset cursor: pass the previous page's last `created_at`. */
  createdBefore?: string;
  limit?: number;
}

/**
 * The tenant's runs, newest first, from the control plane's list endpoint.
 * Callers that can render a partial surface should catch and degrade, as
 * before — the control plane may be unreachable.
 */
export async function listRuns(
  actor: ActorContext,
  options: ListRunsOptions = {},
): Promise<RunSummary[]> {
  const { data } = await controlPlane(actor).GET("/runs", {
    params: {
      query: {
        ...(options.status === undefined ? {} : { status: options.status }),
        ...(options.agentId === undefined ? {} : { agent_id: options.agentId }),
        ...(options.createdBefore === undefined
          ? {}
          : { created_before: options.createdBefore }),
        limit: options.limit ?? 100,
      },
    },
  });
  return data?.runs ?? [];
}

export async function getRun(actor: ActorContext, runId: string): Promise<RunView | null> {
  const { data } = await controlPlane(actor).GET("/runs/{run_id}", {
    params: { path: { run_id: runId } },
  });
  return data ?? null;
}

/**
 * Whether a binding target is a rehearsal copy. Registry rehearsal bindings
 * carry the environment id with a /sandbox suffix; the table-backed dev
 * fallback binds rehearsal to the runtime stub's `stub-local`. Runs created before
 * the environment_id column carry an empty target and read as production —
 * the runtime re-checks every rehearsal-only operation server-side anyway.
 */
export function isRehearsalTarget(environmentId: string | null | undefined): boolean {
  if (!environmentId) return false;
  return environmentId.endsWith("/sandbox") || environmentId === "stub-local";
}

/** The Agent a run was launched from, resolved through the control plane. */
export async function agentIdForRun(
  actor: ActorContext,
  runId: string,
): Promise<string | undefined> {
  const view = await getRun(actor, runId);
  return view?.agent_id ?? undefined;
}

export interface CreateRunInput {
  goal: string;
  environmentId: string;
  budgetUsd: string;
  /** Connector capabilities requested from the selected immutable binding. */
  tools?: string[];
  /** The agent's authored step list; becomes the run's initial plan (a
   * "checkpoint: ..." line becomes an approval gate on the next step). */
  instructions?: string[];
  agentId?: string;
  /** @deprecated Use agentId. Accepted only at the frozen compatibility seam. */
  routineId?: string;
  /** Website user starting the run, for the "my runs" filter. */
  startedById?: string;
  /** Trigger class; console launches are manual. */
  startedVia?: "manual" | "schedule" | "event";
  requirePlanApproval?: boolean;
}

/** Create a run at the edge; attribution rides the request and lands in the
 * run projection, so the list sees it with no website-side bookkeeping. */
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
      tools: input.tools ?? [],
      instructions: input.instructions ?? [],
      agent_id: input.agentId ?? input.routineId ?? null,
      started_by: input.startedById ?? null,
      started_via: input.startedVia ?? "manual",
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
  return { runId: data.run_id, status: data.status };
}
