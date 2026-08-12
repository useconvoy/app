/**
 * The human actions on runs, one server action per typed verb.
 *
 * Every action follows the same spine: verified session -> tenant resolved
 * from the org -> server-side permission check -> typed client call -> a
 * discriminated result. Writes are accepted and confirmed by events, so
 * nothing here flips UI state: a 409 comes back as `conflict` and is never
 * retried, and a 404 stays a 404. Steers are the single optimistic surface,
 * so steerRun alone returns the steer id for the composer's "Guidance
 * sent". Actor attribution rides the bridge headers on every call; human
 * actions never ride a service identity.
 */
"use server";

import { controlPlane } from "@/lib/api/client";
import { getRun, isRehearsalTarget } from "@/lib/api/runs";
import { copy } from "@/lexicon";
import { can } from "@/lib/permissions";
import { isAssignedToRoutine } from "@/lib/routines/queries";
import type { CommandResult, SteerMode, SteerResult } from "./command-types";
import { requireRunContext, type RunContext } from "./context";

type Guard =
  | { ok: true; context: RunContext; environmentId: string }
  | { ok: false; refused: { kind: "refused"; message: string } };

/**
 * The shared gate: active member with answer_checkpoints (member and up;
 * pause/land/steer deliberately ride the same grant), plus assignment
 * awareness. A member acting on a run that maps to an Agent must be
 * assigned to that Agent; admins and operators pass regardless. The run's
 * attribution (agent, binding target) comes from the control plane's own
 * record, so runs started outside this process guard identically.
 */
async function guardRunAction(runId: string): Promise<Guard> {
  const context = await requireRunContext();
  const { membership, session } = context;
  if (!can("answer_checkpoints", membership.role, membership.capabilities)) {
    return { ok: false, refused: { kind: "refused", message: copy.viewersCannotAct } };
  }
  const view = await getRun(context.actor, runId);
  const routineId = view?.agent_id ?? undefined;
  if (routineId && membership.role !== "admin" && membership.role !== "operator") {
    const assigned = await isAssignedToRoutine(session.orgId, session.userId, routineId);
    if (!assigned) {
      return { ok: false, refused: { kind: "refused", message: copy.assignedToSomeoneElse } };
    }
  }
  return { ok: true, context, environmentId: view?.environment_id ?? "" };
}

/** Map a non-2xx edge response to the discriminated result. */
function commandFailure(
  status: number,
  error: unknown,
): Exclude<CommandResult, { kind: "accepted" }> {
  if (status === 404) return { kind: "notFound" };
  if (status === 409) {
    const detail = (error as { detail?: unknown } | null)?.detail;
    return {
      kind: "conflict",
      detail: typeof detail === "string" ? detail : "The run has moved on since this was shown.",
    };
  }
  // 422 and friends: surface as a refusal in a plain sentence; the request
  // itself was malformed, not the run's state.
  const detail = (error as { detail?: unknown } | null)?.detail;
  return {
    kind: "refused",
    message: typeof detail === "string" ? detail : copy.platformRefused,
  };
}

export async function pauseRun(runId: string): Promise<CommandResult> {
  const guard = await guardRunAction(runId);
  if (!guard.ok) return guard.refused;
  const { error, response } = await controlPlane(guard.context.actor).POST(
    "/runs/{run_id}/pause",
    { params: { path: { run_id: runId } } },
  );
  return error ? commandFailure(response.status, error) : { kind: "accepted" };
}

export async function resumeRun(runId: string): Promise<CommandResult> {
  const guard = await guardRunAction(runId);
  if (!guard.ok) return guard.refused;
  const { error, response } = await controlPlane(guard.context.actor).POST(
    "/runs/{run_id}/resume",
    { params: { path: { run_id: runId } } },
  );
  return error ? commandFailure(response.status, error) : { kind: "accepted" };
}

export async function landRun(runId: string): Promise<CommandResult> {
  const guard = await guardRunAction(runId);
  if (!guard.ok) return guard.refused;
  const { error, response } = await controlPlane(guard.context.actor).POST(
    "/runs/{run_id}/land",
    { params: { path: { run_id: runId } } },
  );
  return error ? commandFailure(response.status, error) : { kind: "accepted" };
}

/** The one optimistic surface: returns the steer id so the composer can
 * render "Guidance sent" immediately, confirmed later by steer_received. */
export async function steerRun(
  runId: string,
  mode: SteerMode,
  body: string,
): Promise<SteerResult> {
  const guard = await guardRunAction(runId);
  if (!guard.ok) return guard.refused;
  if (body.trim().length === 0) {
    return { kind: "refused", message: copy.platformRefused };
  }
  const { data, error, response } = await controlPlane(guard.context.actor).POST(
    "/runs/{run_id}/steer",
    { params: { path: { run_id: runId } }, body: { mode, body: body.trim() } },
  );
  if (error || !data) return commandFailure(response.status, error);
  return { kind: "accepted", steerId: data.steer_id };
}

/**
 * Approve or reject exactly the plan version the human saw rendered. A 409
 * means the plan moved: the caller shows v(n+1), never retries.
 * Rejections must say why, checked here before the wire does.
 */
export async function approvePlan(
  runId: string,
  planVersion: number,
  approve: boolean,
  reason?: string,
): Promise<CommandResult> {
  const guard = await guardRunAction(runId);
  if (!guard.ok) return guard.refused;
  const trimmed = reason?.trim();
  if (!approve && !trimmed) {
    return { kind: "refused", message: copy.rejectReasonRequired };
  }
  const { error, response } = await controlPlane(guard.context.actor).POST(
    "/runs/{run_id}/plan/approve",
    {
      params: { path: { run_id: runId } },
      body: { plan_version: planVersion, approve, reason: trimmed ?? null },
    },
  );
  return error ? commandFailure(response.status, error) : { kind: "accepted" };
}

/**
 * Answer one step's open checkpoint. `atVirtual` scripts a simulated
 * answer at a rehearsal moment and is refused outside rehearsals; the
 * runtime re-checks either way. Answers render only from the confirming
 * gate_answered event, never optimistically.
 */
export async function respondToGate(
  runId: string,
  stepId: string,
  response: string,
  atVirtual?: string,
): Promise<CommandResult> {
  const guard = await guardRunAction(runId);
  if (!guard.ok) return guard.refused;
  if (atVirtual && !isRehearsalTarget(guard.environmentId)) {
    return { kind: "refused", message: copy.rehearsalOnlyAnswerAt };
  }
  const { error, response: httpResponse } = await controlPlane(guard.context.actor).POST(
    "/runs/{run_id}/steps/{step_id}/respond",
    {
      params: { path: { run_id: runId, step_id: stepId } },
      body: { response, at_virtual: atVirtual ?? null },
    },
  );
  return error ? commandFailure(httpResponse.status, error) : { kind: "accepted" };
}

/**
 * Fast-forward a rehearsal run's virtual clock. The rehearsal-only guard in
 * the UI is convenience; the runtime enforces for real and its 409 detail
 * comes back as a plain sentence.
 */
export async function advanceClock(runId: string, toIso: string): Promise<CommandResult> {
  const guard = await guardRunAction(runId);
  if (!guard.ok) return guard.refused;
  const { error, response } = await controlPlane(guard.context.actor).POST(
    "/runs/{run_id}/clock/advance",
    { params: { path: { run_id: runId } }, body: { to: toIso } },
  );
  return error ? commandFailure(response.status, error) : { kind: "accepted" };
}
