// @vitest-environment node
/**
 * The notifier against a real Postgres: exactly-once writes over a recorded
 * feed replayed twice, monotonic cursor, open_gates upkeep, prefs
 * suppression through the 0003 org-read policy, and the deadline sweep's
 * one-shot notice. Seeding follows the db-rls suite: orgs/users/memberships
 * ride the owner connection; everything else goes through the same
 * withOrgContext the production code uses. The feed replays the recorded
 * run-gated fixture; no invented events.
 */
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import pg from "pg";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { withOrgContext } from "@/lib/db";
import { consumerTick, notifyPromotionRequested } from "@/notifier/consumer";
import type { EventFeed, RunEvent } from "@/notifier/feed";
import { sweepTick } from "@/notifier/sweep";

import gatedFixture from "../fixtures/sse/run-gated.json";

const ADMIN_DSN = process.env.WEBSITE_PG_ADMIN_DSN;
const APP_DSN =
  process.env.WEBSITE_PG_DSN ??
  "postgresql://convoy_website_app:convoy_website_app@localhost:5440/convoy_website";
const WEBSITE_ROOT = new URL("../..", import.meta.url).pathname;

const suffix = randomUUID().slice(0, 8);
const orgId = randomUUID();
const owner = randomUUID();
const approverA = randomUUID();
const approverB = randomUUID();
const watcher = randomUUID();
const operator = randomUUID();

const ROUTINE_ID = "routine-access-review";
const events = gatedFixture as unknown as RunEvent[];
const RUN_ID = events[0]!.run_id;
const resolveRoutineId = () => ROUTINE_ID;

/** The recorded feed with org seqs; respects the cursor like D8 will. */
function recordedFeed(feedEvents: RunEvent[]): EventFeed {
  return {
    pull: async (after) =>
      feedEvents
        .map((event, index) => ({ event, orgSeq: index + 1 }))
        .filter((entry) => entry.orgSeq > after),
  };
}

/**
 * A restarted fan-in feed: replays everything regardless of the cursor,
 * exactly what happens when in-memory per-run cursors reset. Idempotency
 * and GREATEST must absorb it.
 */
function replayEverythingFeed(feedEvents: RunEvent[]): EventFeed {
  return {
    pull: async () => feedEvents.map((event, index) => ({ event, orgSeq: index + 1 })),
  };
}

describe.skipIf(!ADMIN_DSN)("notifier (real Postgres)", () => {
  let admin: pg.Client;
  let teamId: string;

  async function countNotifications(where = "", params: unknown[] = []): Promise<number> {
    const { rows } = await admin.query<{ n: string }>(
      `SELECT count(*)::text AS n FROM notifications WHERE org_id = $1 ${where}`,
      [orgId, ...params],
    );
    return Number(rows[0]!.n);
  }

  async function cursorSeq(): Promise<number> {
    const { rows } = await admin.query<{ cursor_seq: string }>(
      "SELECT cursor_seq FROM notifier_state WHERE org_id = $1",
      [orgId],
    );
    return Number(rows[0]?.cursor_seq ?? 0);
  }

  beforeAll(async () => {
    execFileSync(process.execPath, ["scripts/migrate.mjs"], {
      cwd: WEBSITE_ROOT,
      env: { ...process.env, WEBSITE_PG_ADMIN_DSN: ADMIN_DSN as string },
      stdio: "pipe",
    });
    process.env.WEBSITE_PG_DSN = APP_DSN;

    admin = new pg.Client({ connectionString: ADMIN_DSN });
    await admin.connect();
    await admin.query("INSERT INTO organizations (id, tenant_id, name) VALUES ($1, $2, $3)", [
      orgId,
      `tenant-${suffix}`,
      `Notifier Org ${suffix}`,
    ]);
    await admin.query(
      `INSERT INTO users (id, email, name) VALUES
        ($1, 'owner-${suffix}@example.com', 'J. Owner'),
        ($2, 'appa-${suffix}@example.com', 'J. Approver'),
        ($3, 'appb-${suffix}@example.com', 'K. Approver'),
        ($4, 'watch-${suffix}@example.com', 'J. Watcher'),
        ($5, 'op-${suffix}@example.com', 'J. Operator')`,
      [owner, approverA, approverB, watcher, operator],
    );
    await admin.query(
      `INSERT INTO memberships (org_id, user_id, role, capabilities) VALUES
        ($1, $2, 'member', '{}'),
        ($1, $3, 'member', '{approver}'),
        ($1, $4, 'member', '{approver}'),
        ($1, $5, 'viewer', '{}'),
        ($1, $6, 'operator', '{}')`,
      [orgId, owner, approverA, approverB, watcher, operator],
    );

    // Team + assignments + prefs are written the way the product writes
    // them: under the org's own RLS context.
    await withOrgContext({ orgId, userId: owner }, async (client) => {
      const { rows } = await client.query<{ id: string }>(
        "INSERT INTO teams (org_id, name) VALUES ($1, $2) RETURNING id",
        [orgId, `Reviewers ${suffix}`],
      );
      teamId = rows[0]!.id;
      await client.query(
        "INSERT INTO team_memberships (team_id, user_id) VALUES ($1, $2), ($1, $3)",
        [teamId, approverA, approverB],
      );
      await client.query(
        `INSERT INTO routine_assignments (org_id, routine_id, assignee_type, assignee_id, relationship)
         VALUES ($1, $2, 'user', $3, 'owner'),
                ($1, $2, 'team', $4, 'approver'),
                ($1, $2, 'user', $5, 'watcher')`,
        [orgId, ROUTINE_ID, owner, teamId, watcher],
      );
    });
    // The watcher opts out of run_landed in-app notices (own-row write).
    await withOrgContext({ orgId, userId: watcher }, (client) =>
      client.query(
        `INSERT INTO notification_prefs (user_id, org_id, class, channels)
         VALUES ($1, $2, 'run_landed', '{}')`,
        [watcher, orgId],
      ),
    );
  }, 30_000);

  afterAll(async () => {
    await globalThis.__convoyWebsitePool?.end();
    globalThis.__convoyWebsitePool = undefined;
    if (!admin) return;
    await admin.query("DELETE FROM notifications WHERE org_id = $1", [orgId]);
    await admin.query("DELETE FROM notifier_state WHERE org_id = $1", [orgId]);
    await admin.query("DELETE FROM open_gates WHERE org_id = $1", [orgId]);
    await admin.query("DELETE FROM notification_prefs WHERE org_id = $1", [orgId]);
    await admin.query("DELETE FROM routine_assignments WHERE org_id = $1", [orgId]);
    await admin.query(
      "DELETE FROM team_memberships tm USING teams t WHERE t.id = tm.team_id AND t.org_id = $1",
      [orgId],
    );
    await admin.query("DELETE FROM teams WHERE org_id = $1", [orgId]);
    await admin.query("DELETE FROM memberships WHERE org_id = $1", [orgId]);
    await admin.query("DELETE FROM users WHERE id = ANY($1)", [
      [owner, approverA, approverB, watcher, operator],
    ]);
    await admin.query("DELETE FROM organizations WHERE id = $1", [orgId]);
    await admin.end();
  });

  it("gate_opened creates the open_gates row before the answer clears it", async () => {
    // Replay only up to the gate_opened event first.
    const gateIndex = events.findIndex((event) => event.type === "gate_opened");
    const prefix = events.slice(0, gateIndex + 1);
    const result = await consumerTick(orgId, recordedFeed(prefix), { resolveRoutineId });
    expect(result.events).toBe(prefix.length);

    const gates = await admin.query(
      "SELECT step_id, deadline, notified_at FROM open_gates WHERE org_id = $1 AND run_id = $2",
      [orgId, RUN_ID],
    );
    expect(gates.rowCount).toBe(1);
    expect(gates.rows[0].step_id).toBe("step-2");
    expect(gates.rows[0].notified_at).toBeNull();
  });

  it("consumes the recorded feed exactly once across repeated replays", async () => {
    // Finish the recorded feed (a restart replays it from the beginning).
    const first = await consumerTick(orgId, replayEverythingFeed(events), { resolveRoutineId });
    expect(first.cursor).toBe(events.length);

    // checkpoint_opened -> approver team (2) + owner; run_landed -> owner
    // only (the watcher opted out via prefs).
    expect(await countNotifications("AND class = 'checkpoint_opened'")).toBe(3);
    expect(await countNotifications("AND class = 'run_landed'")).toBe(1);
    expect(await countNotifications("AND user_id = $2", [watcher])).toBe(0);
    const total = await countNotifications();
    expect(total).toBe(4);

    // Second full replay: zero new rows, same cursor.
    const second = await consumerTick(orgId, replayEverythingFeed(events), { resolveRoutineId });
    expect(second.notifications).toBe(0);
    expect(second.cursor).toBe(events.length);
    expect(await countNotifications()).toBe(total);

    // The recorded answer cleared the gate.
    const gates = await admin.query("SELECT 1 FROM open_gates WHERE org_id = $1 AND run_id = $2", [
      orgId,
      RUN_ID,
    ]);
    expect(gates.rowCount).toBe(0);
  });

  it("never moves the cursor backward", async () => {
    const before = await cursorSeq();
    expect(before).toBe(events.length);
    // An empty pull and a stale replay both leave the cursor where it was.
    const idle = await consumerTick(orgId, { pull: async () => [] }, { resolveRoutineId });
    expect(idle.cursor).toBe(before);
    await consumerTick(orgId, replayEverythingFeed(events.slice(0, 3)), { resolveRoutineId });
    expect(await cursorSeq()).toBe(before);
  });

  it("sweeps an approaching deadline once, to approvers and owners", async () => {
    const deadline = new Date(Date.now() + 30 * 60 * 1000);
    await withOrgContext({ orgId }, (client) =>
      client.query(
        "INSERT INTO open_gates (org_id, run_id, step_id, deadline) VALUES ($1, $2, $3, $4)",
        [orgId, "run-sweep-1", "step-9", deadline],
      ),
    );

    const first = await sweepTick(orgId, { resolveRoutineId });
    expect(first.notices).toBe(1);
    expect(await countNotifications("AND class = 'checkpoint_deadline'")).toBe(3);
    const { rows } = await admin.query(
      "SELECT cta_url, notified_at FROM open_gates og JOIN notifications n ON n.run_id = og.run_id WHERE og.org_id = $1 AND og.run_id = 'run-sweep-1' LIMIT 1",
      [orgId],
    );
    expect(rows[0].notified_at).not.toBeNull();
    expect(rows[0].cta_url).toBe("/app/runs/run-sweep-1#respond-step-9");

    // Second sweep: the notified_at marker suppresses a repeat.
    const second = await sweepTick(orgId, { resolveRoutineId });
    expect(second.notices).toBe(0);
    expect(await countNotifications("AND class = 'checkpoint_deadline'")).toBe(3);
  });

  it("enqueues promotion review notices to operators, idempotently", async () => {
    const first = await notifyPromotionRequested(orgId, ROUTINE_ID, RUN_ID);
    expect(first).toBe(1); // the seeded operator
    const again = await notifyPromotionRequested(orgId, ROUTINE_ID, RUN_ID);
    expect(again).toBe(0);
    expect(await countNotifications("AND class = 'promotion_requested'")).toBe(1);
    expect(
      await countNotifications("AND class = 'promotion_requested' AND user_id = $2", [operator]),
    ).toBe(1);
  });
});
