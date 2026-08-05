/**
 * The human actions on runs (W2), one server action per typed verb.
 *
 * Every action follows the same spine: verified session -> tenant resolved
 * from the org -> server-side permission check -> typed client call -> a
 * discriminated result. Writes are 202 + events (CLAUDE.md rule 7): nothing
 * here flips UI state, a 409 is returned as `conflict` and never retried,
 * and a 404 stays a 404. Steers are the single optimistic surface, so
 * steerRun alone returns the steer id for the composer's "Guidance sent".
 * Actor attribution rides the bridge headers on every call (rule 8).
 */
"use server";

import { controlPlane, controlPlaneUrl, sseHeaders, type ActorContext } from "@/lib/api/client";
import { routineIdForRun } from "@/lib/api/runs";
import { copy } from "@/lexicon";
import { can } from "@/lib/permissions";
import { isRehearsalRun, recordRunTarget } from "@/lib/routines/data";
import { isAssignedToRoutine } from "@/lib/routines/queries";
import type { CommandResult, SteerMode, SteerResult } from "./command-types";
import { requireRunContext, type RunContext } from "./context";

type Guard =
  | { ok: true; context: RunContext }
  | { ok: false; refused: { kind: "refused"; message: string } };

/**
 * The shared gate: active member with answer_checkpoints (member and up;
 * pause/land/steer ride the same grant per DESIGN §2), plus assignment
 * awareness. A member acting on a run that maps to a routine must be
 * assigned to that routine; admins and operators pass regardless.
 */
async function guardRunAction(runId: string): Promise<Guard> {
  const context = await requireRunContext();
  const { membership, session } = context;
  if (!can("answer_checkpoints", membership.role, membership.capabilities)) {
    return { ok: false, refused: { kind: "refused", message: copy.viewersCannotAct } };
  }
  const routineId = routineIdForRun(runId);
  if (routineId && membership.role !== "admin" && membership.role !== "operator") {
    const assigned = await isAssignedToRoutine(session.orgId, session.userId, routineId);
    if (!assigned) {
      return { ok: false, refused: { kind: "refused", message: copy.assignedToSomeoneElse } };
    }
  }
  return { ok: true, context };
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
 * means the plan moved: the caller shows v(n+1), never retries (rule 7).
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
 * Whether the run is a rehearsal, checked server-side. The console's own
 * runs are recorded at start; runs first seen through their detail page are
 * checked against the stream's sandbox flag (the first event carries it)
 * and remembered. TODO(runtime-D8): the run list's sandbox flag replaces
 * this probe.
 */
async function isRehearsal(actor: ActorContext, runId: string): Promise<boolean> {
  if (isRehearsalRun(runId)) return true;
  const controller = new AbortController();
  try {
    const upstream = await fetch(
      `${controlPlaneUrl()}/runs/${encodeURIComponent(runId)}/events?after=0`,
      {
        headers: { ...sseHeaders(actor), Accept: "text/event-stream" },
        cache: "no-store",
        signal: controller.signal,
      },
    );
    if (!upstream.ok || !upstream.body) return false;
    const reader = upstream.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    // A handful of reads is plenty: the first frame arrives immediately on
    // any run that exists.
    for (let i = 0; i < 8; i += 1) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const frame = buffer.match(/^data: (.*)$/m);
      if (frame) {
        const event = JSON.parse(frame[1]!) as { sandbox?: unknown };
        if (event.sandbox === true) recordRunTarget(runId, "rehearsal");
        return event.sandbox === true;
      }
    }
    return false;
  } catch {
    return false;
  } finally {
    controller.abort();
  }
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
  if (atVirtual && !(await isRehearsal(guard.context.actor, runId))) {
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
