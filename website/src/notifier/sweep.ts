/**
 * The deadline sweep: open_gates rows whose deadline falls
 * inside the warning window and that have not been noticed yet get a
 * checkpoint_deadline notification, once. The window math is a pure
 * function so the boundaries are unit-testable; the tick wraps it in the
 * org's RLS context.
 *
 * The sweep is the notifier's second (and last) notification writer; its
 * synthetic event id `run_id:step_id:deadline` is deterministic, so even a
 * crash between insert and the notified_at update replays harmlessly.
 */
import { withOrgContext } from "../lib/db";
import { notificationTitles } from "../lexicon";
import { loadRoutingWorld, resolveRecipients } from "./routing";

/** Default warning window: notice deadlines within the next 60 minutes. */
export const DEFAULT_DEADLINE_WINDOW_MS = 60 * 60 * 1000;

export interface OpenGate {
  runId: string;
  stepId: string;
  deadline: Date | null;
  notifiedAt: Date | null;
}

/**
 * Which gates deserve a deadline notice now. A gate qualifies when it has
 * a deadline, has not been noticed, and its deadline is at most `windowMs`
 * away (boundary inclusive). Overdue-but-still-open gates qualify too: a
 * missed deadline on an unanswered checkpoint is the most urgent case.
 */
export function gatesDueForNotice(
  gates: readonly OpenGate[],
  now: Date,
  windowMs: number = DEFAULT_DEADLINE_WINDOW_MS,
): OpenGate[] {
  return gates.filter((row) => {
    if (row.deadline === null || row.notifiedAt !== null) return false;
    return row.deadline.getTime() - now.getTime() <= windowMs;
  });
}

/** Deterministic idempotency key for a gate's single deadline notice. */
export function deadlineEventId(runId: string, stepId: string): string {
  return `${runId}:${stepId}:deadline`;
}

export interface SweepOptions {
  now?: Date;
  windowMs?: number;
  /** Run id -> routine id; defaults to the run directory seam in src/lib/api/runs. */
  resolveRoutineId?: (runId: string) => string | undefined;
}

export interface SweepTickResult {
  /** Gates noticed this tick. */
  notices: number;
}

/** One sweep over an org's open gates. */
export async function sweepTick(orgId: string, options: SweepOptions = {}): Promise<SweepTickResult> {
  const now = options.now ?? new Date();
  const windowMs = options.windowMs ?? DEFAULT_DEADLINE_WINDOW_MS;
  const resolveRoutineId = options.resolveRoutineId ?? (() => undefined);

  return withOrgContext({ orgId }, async (client) => {
    const { rows } = await client.query<{
      runId: string;
      stepId: string;
      deadline: Date | null;
      notifiedAt: Date | null;
    }>(
      `SELECT run_id AS "runId", step_id AS "stepId", deadline, notified_at AS "notifiedAt"
         FROM open_gates WHERE deadline IS NOT NULL AND notified_at IS NULL`,
    );

    let notices = 0;
    for (const gate of gatesDueForNotice(rows, now, windowMs)) {
      const routineId = resolveRoutineId(gate.runId);
      const world = await loadRoutingWorld(client, orgId, routineId, "checkpoint_deadline");
      const recipients = resolveRecipients(world, "checkpoint_deadline", routineId);
      if (recipients.length > 0) {
        // Bare ON CONFLICT: see insertNotifications in consumer.ts for why
        // the (event_id, user_id) arbiter is not named under RLS.
        await client.query(
          `INSERT INTO notifications (org_id, user_id, event_id, run_id, class, title, cta_url)
           SELECT $1, u, $2, $3, 'checkpoint_deadline', $4, $5 FROM unnest($6::uuid[]) AS u
           ON CONFLICT DO NOTHING`,
          [
            orgId,
            deadlineEventId(gate.runId, gate.stepId),
            gate.runId,
            notificationTitles.deadline(),
            `/app/runs/${gate.runId}#respond-${gate.stepId}`,
            recipients,
          ],
        );
      }
      await client.query(
        `UPDATE open_gates SET notified_at = $3
          WHERE run_id = $1 AND step_id = $2 AND notified_at IS NULL`,
        [gate.runId, gate.stepId, now],
      );
      notices += 1;
    }
    return { notices };
  });
}
