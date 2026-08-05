// @vitest-environment node
/**
 * Feedback capture against a real Postgres: the submit round-trip lands a
 * row with learning_status "new", the learning lifecycle moves new -> queued ->
 * consumed (and only along that path), the routine-level handoff queues
 * exactly the routine's rows, and RLS keeps every row invisible to another
 * org. Seeding follows the db-rls suite: orgs/users/memberships ride the
 * owner connection; everything else goes through the same helpers the
 * production code uses.
 */
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import pg from "pg";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { registerRun } from "@/lib/api/runs";
import { withOrgContext } from "@/lib/db";
import {
  consumeRoutineFeedback,
  insertFeedback,
  listFeedbackForOrg,
  listFeedbackForRoutine,
  listFeedbackForRun,
  markFeedbackConsumed,
  markFeedbackQueued,
  queueRoutineFeedback,
} from "@/lib/feedback/queries";

const ADMIN_DSN = process.env.WEBSITE_PG_ADMIN_DSN;
const APP_DSN =
  process.env.WEBSITE_PG_DSN ??
  "postgresql://convoy_website_app:convoy_website_app@localhost:5440/convoy_website";
const WEBSITE_ROOT = new URL("../..", import.meta.url).pathname;

const suffix = randomUUID().slice(0, 8);
const orgA = randomUUID();
const orgB = randomUUID();
const author = randomUUID();
const outsider = randomUUID();

const TENANT_A = `tenant-fb-${suffix}`;
const RUN_REVIEW = `run-fb-review-${suffix}`;
const RUN_VENDOR = `run-fb-vendor-${suffix}`;

describe.skipIf(!ADMIN_DSN)("feedback capture (real Postgres)", () => {
  let admin: pg.Client;

  async function statusOf(id: string): Promise<string> {
    const { rows } = await admin.query<{ learning_status: string }>(
      "SELECT learning_status FROM feedback WHERE id = $1",
      [id],
    );
    return rows[0]!.learning_status;
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
    await admin.query(
      `INSERT INTO organizations (id, tenant_id, name) VALUES
        ($1, $3, 'Feedback Org ${suffix}'), ($2, $4, 'Other Org ${suffix}')`,
      [orgA, orgB, TENANT_A, `tenant-fb-b-${suffix}`],
    );
    await admin.query(
      `INSERT INTO users (id, email, name) VALUES
        ($1, 'author-${suffix}@example.com', 'J. Doe'),
        ($2, 'outsider-${suffix}@example.com', 'B. One')`,
      [author, outsider],
    );
    await admin.query(
      `INSERT INTO memberships (org_id, user_id, role) VALUES
        ($1, $3, 'member'), ($2, $4, 'admin')`,
      [orgA, orgB, author, outsider],
    );

    // The run directory maps runs to routines for the routine-level reads.
    registerRun({ runId: RUN_REVIEW, tenantId: TENANT_A, routineId: "routine-access-review" });
    registerRun({ runId: RUN_VENDOR, tenantId: TENANT_A, routineId: "routine-vendor-check" });
  }, 30_000);

  afterAll(async () => {
    await globalThis.__convoyWebsitePool?.end();
    globalThis.__convoyWebsitePool = undefined;
    if (!admin) return;
    await admin.query("DELETE FROM feedback WHERE org_id = ANY($1)", [[orgA, orgB]]);
    await admin.query("DELETE FROM memberships WHERE org_id = ANY($1)", [[orgA, orgB]]);
    await admin.query("DELETE FROM users WHERE id = ANY($1)", [[author, outsider]]);
    await admin.query("DELETE FROM organizations WHERE id = ANY($1)", [[orgA, orgB]]);
    await admin.end();
  });

  let ratingId: string;
  let commentId: string;
  let vendorId: string;

  it("lands a submitted row with learning_status new", async () => {
    ratingId = await insertFeedback(orgA, author, {
      runId: RUN_REVIEW,
      kind: "rating",
      rating: 4,
    });
    commentId = await insertFeedback(orgA, author, {
      runId: RUN_REVIEW,
      stepId: "step-2",
      kind: "comment",
      body: "The memo for the contractor account named the wrong manager.",
    });
    vendorId = await insertFeedback(orgA, author, {
      runId: RUN_VENDOR,
      kind: "correction",
      body: "Ask each vendor once, then wait a week.",
    });

    const items = await listFeedbackForRun(orgA, RUN_REVIEW);
    expect(items).toHaveLength(2);
    expect(items.every((item) => item.learningStatus === "new")).toBe(true);
    const comment = items.find((item) => item.id === commentId)!;
    expect(comment.authorName).toBe("J. Doe");
    expect(comment.stepId).toBe("step-2");
    expect(comment.kind).toBe("comment");
  });

  it("resolves routine-level reads through the run directory", async () => {
    const review = await listFeedbackForRoutine(orgA, "routine-access-review");
    expect(review.map((item) => item.id).sort()).toEqual([commentId, ratingId].sort());
    const vendor = await listFeedbackForRoutine(orgA, "routine-vendor-check");
    expect(vendor.map((item) => item.id)).toEqual([vendorId]);
  });

  it("queues a routine's new feedback on approval, leaving other routines alone", async () => {
    const queued = await queueRoutineFeedback(orgA, "routine-access-review");
    expect(queued).toBe(2);
    expect(await statusOf(ratingId)).toBe("queued");
    expect(await statusOf(commentId)).toBe("queued");
    expect(await statusOf(vendorId)).toBe("new");
  });

  it("moves queued to consumed, and only along the lifecycle path", async () => {
    // consumed only from queued: the still-new vendor row does not move.
    expect(await markFeedbackConsumed(orgA, [vendorId])).toBe(0);
    expect(await statusOf(vendorId)).toBe("new");

    // queued only from new: re-queueing consumed rows is a no-op.
    const consumed = await consumeRoutineFeedback(orgA, "routine-access-review");
    expect(consumed).toBe(2);
    expect(await statusOf(ratingId)).toBe("consumed");
    expect(await markFeedbackQueued(orgA, [ratingId])).toBe(0);
    expect(await statusOf(ratingId)).toBe("consumed");
  });

  it("keeps every row invisible to another org (RLS)", async () => {
    expect(await listFeedbackForRun(orgB, RUN_REVIEW)).toEqual([]);
    expect(await listFeedbackForOrg(orgB)).toEqual([]);
    // Cross-org writes are no-ops too: org B cannot move org A's rows.
    expect(await markFeedbackQueued(orgB, [vendorId])).toBe(0);
    expect(await statusOf(vendorId)).toBe("new");
    // Direct SQL under org B's context sees nothing either.
    const visible = await withOrgContext({ orgId: orgB, userId: outsider }, async (client) => {
      const { rows } = await client.query("SELECT id FROM feedback");
      return rows;
    });
    expect(visible).toEqual([]);
  });
});
