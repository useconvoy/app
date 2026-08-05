/**
 * The consumer loop body: pull feed events after the org cursor, match
 * rules times routing, write notifications exactly once, and keep the
 * open_gates table current for the deadline sweep.
 *
 * Iron rule 9 discipline: this module (plus sweep.ts) is the ONLY writer of
 * notifications. Every insert is idempotent on (event_id, user_id) via
 * ON CONFLICT DO NOTHING, and the org cursor only ever moves forward
 * (GREATEST), never backward, in the same transaction as the writes it
 * covers. Replaying an already-consumed feed is therefore always safe:
 * zero new rows, cursor unchanged.
 */
import type { PoolClient } from "pg";

import { withOrgContext } from "../lib/db";
import { notificationTitles } from "../lexicon";
import type { EventFeed } from "./feed";
import { routineIdForRunId } from "./feed";
import { candidatesForEvent, type NotificationCandidate } from "./rules";
import { loadRoutingWorld, resolveRecipients, type RoutingWorld } from "./routing";

export interface ConsumerOptions {
  /** Run id -> routine id; defaults to the W1 run directory seam. */
  resolveRoutineId?: (runId: string) => string | undefined;
}

export interface ConsumerTickResult {
  /** Feed events processed this tick. */
  events: number;
  /** Notification rows actually inserted (conflicts excluded). */
  notifications: number;
  /** Org cursor after the tick. */
  cursor: number;
}

/**
 * Insert one candidate for many users; returns rows actually written.
 *
 * The idempotency arbiter is the UNIQUE (event_id, user_id) constraint. The
 * ON CONFLICT clause deliberately names no target: with a named target,
 * Postgres applies the table's SELECT policy to the proposed rows, and the
 * notifier's org-only context (no user id) fails the own-inbox SELECT
 * policy. Bare DO NOTHING skips any unique violation without that check;
 * (event_id, user_id) is the only realistic arbiter since the primary key
 * is generated per insert.
 */
async function insertNotifications(
  client: PoolClient,
  orgId: string,
  userIds: string[],
  candidate: NotificationCandidate,
): Promise<number> {
  if (userIds.length === 0) return 0;
  const result = await client.query(
    `INSERT INTO notifications (org_id, user_id, event_id, run_id, class, title, cta_url)
     SELECT $1, u, $2, $3, $4, $5, $6 FROM unnest($7::uuid[]) AS u
     ON CONFLICT DO NOTHING`,
    [
      orgId,
      candidate.eventId,
      candidate.runId,
      candidate.notificationClass,
      candidate.title,
      candidate.ctaUrl,
      userIds,
    ],
  );
  return result.rowCount ?? 0;
}

/** Read (and create on first sight) the org's feed cursor. */
async function readCursor(client: PoolClient, orgId: string): Promise<number> {
  await client.query(
    "INSERT INTO notifier_state (org_id, cursor_seq) VALUES ($1, 0) ON CONFLICT (org_id) DO NOTHING",
    [orgId],
  );
  const { rows } = await client.query<{ cursor_seq: string }>(
    "SELECT cursor_seq FROM notifier_state WHERE org_id = $1",
    [orgId],
  );
  return Number(rows[0]?.cursor_seq ?? 0);
}

/**
 * One consumer tick for one org. The cursor read, the feed pull, and the
 * write transaction are sequential; the feed pull happens outside the write
 * transaction so slow network reads never hold row locks. Idempotent writes
 * plus the monotonic cursor make the read-pull-write split safe.
 */
export async function consumerTick(
  orgId: string,
  feed: EventFeed,
  options: ConsumerOptions = {},
): Promise<ConsumerTickResult> {
  const resolveRoutineId = options.resolveRoutineId ?? routineIdForRunId;
  const cursor = await withOrgContext({ orgId }, (client) => readCursor(client, orgId));
  const batch = await feed.pull(cursor);
  if (batch.length === 0) return { events: 0, notifications: 0, cursor };

  return withOrgContext({ orgId }, async (client) => {
    let written = 0;
    let maxSeq = cursor;
    // Routing worlds are cached per routine and class within the tick.
    const worlds = new Map<string, RoutingWorld>();

    for (const { event, orgSeq } of batch) {
      if (orgSeq > maxSeq) maxSeq = orgSeq;

      // open_gates upkeep for the deadline sweep. A reopened gate resets
      // its deadline and its notified marker; answers and timeouts clear it.
      if (event.type === "gate_opened") {
        const stepId = typeof event.payload.step_id === "string" ? event.payload.step_id : null;
        const deadline =
          typeof event.payload.deadline === "string" ? event.payload.deadline : null;
        if (stepId) {
          await client.query(
            `INSERT INTO open_gates (org_id, run_id, step_id, deadline)
             VALUES ($1, $2, $3, $4)
             ON CONFLICT (run_id, step_id)
             DO UPDATE SET deadline = EXCLUDED.deadline, notified_at = NULL`,
            [orgId, event.run_id, stepId, deadline],
          );
        }
      } else if (event.type === "gate_answered" || event.type === "gate_timed_out") {
        const stepId = typeof event.payload.step_id === "string" ? event.payload.step_id : null;
        if (stepId) {
          await client.query("DELETE FROM open_gates WHERE run_id = $1 AND step_id = $2", [
            event.run_id,
            stepId,
          ]);
        }
      }

      for (const candidate of candidatesForEvent(event)) {
        const routineId = resolveRoutineId(event.run_id);
        const worldKey = `${routineId ?? ""}:${candidate.notificationClass}`;
        let world = worlds.get(worldKey);
        if (!world) {
          world = await loadRoutingWorld(client, orgId, routineId, candidate.notificationClass);
          worlds.set(worldKey, world);
        }
        const recipients = resolveRecipients(world, candidate.notificationClass, routineId);
        written += await insertNotifications(client, orgId, recipients, candidate);
      }
    }

    // Monotonic cursor advance in the same transaction as the writes.
    const { rows } = await client.query<{ cursor_seq: string }>(
      `UPDATE notifier_state SET cursor_seq = GREATEST(cursor_seq, $2)
        WHERE org_id = $1 RETURNING cursor_seq`,
      [orgId, maxSeq],
    );
    return {
      events: batch.length,
      notifications: written,
      cursor: Number(rows[0]?.cursor_seq ?? maxSeq),
    };
  });
}

/**
 * Direct enqueue for the promotion review flow, which is website-side (W2)
 * and has no runtime event to consume. Routes to promoter-capability
 * members. Idempotent on the synthetic event id, so the flow may call it
 * on every submit. TODO(website-W2): wire this into the promotion submit
 * action.
 */
export async function notifyPromotionRequested(
  orgId: string,
  routineId: string,
  runId: string,
): Promise<number> {
  return withOrgContext({ orgId }, async (client) => {
    const world = await loadRoutingWorld(client, orgId, routineId, "promotion_requested");
    const recipients = resolveRecipients(world, "promotion_requested", routineId);
    return insertNotifications(client, orgId, recipients, {
      eventId: `${runId}:promotion:${routineId}`,
      runId,
      notificationClass: "promotion_requested",
      title: notificationTitles.promotionRequested(),
      ctaUrl: `/app/runs/${runId}#promote`,
    });
  });
}

/**
 * Direct enqueue for the learning packet: an improvement backed by `runId`
 * is ready to review. No source emits this yet. TODO(website-W5): call
 * from the improvements pipeline when it lands.
 */
export async function notifyImprovementReady(
  orgId: string,
  routineId: string,
  runId: string,
): Promise<number> {
  return withOrgContext({ orgId }, async (client) => {
    const world = await loadRoutingWorld(client, orgId, routineId, "improvement_ready");
    const recipients = resolveRecipients(world, "improvement_ready", routineId);
    return insertNotifications(client, orgId, recipients, {
      eventId: `${runId}:improvement:${routineId}`,
      runId,
      notificationClass: "improvement_ready",
      title: notificationTitles.improvementReady(),
      ctaUrl: "/app/learning",
    });
  });
}
