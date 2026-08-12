/**
 * Server-only data access for feedback capture. The
 * feedback table is the website-side capture of the shared core/ Feedback
 * shape that learning/ consumes; learning_status tracks that handoff:
 * new -> queued (a person approved an improvement, the routine's feedback
 * rides along) -> consumed (the shipped change absorbed it).
 *
 * Every query runs through withOrgContext so RLS bounds it; org ids arrive
 * from the verified session, never from client input. Feedback bodies are
 * people's words: they are stored and rendered, never logged or echoed
 * into error messages.
 */
import "server-only";

import type { PoolClient } from "pg";

import { listRuns } from "@/lib/api/runs";
import { orgTenantId } from "@/lib/agents/queries";
import { withOrgContext } from "@/lib/db";
import type { FeedbackKind, LearningStatus } from "@/lexicon";

export interface FeedbackItem {
  id: string;
  runId: string;
  stepId: string | null;
  kind: FeedbackKind;
  rating: number | null;
  body: string | null;
  authorName: string;
  learningStatus: LearningStatus;
  createdAt: Date;
}

export interface InsertFeedbackInput {
  runId: string;
  stepId?: string;
  kind: FeedbackKind;
  rating?: number;
  body?: string;
}

const ITEM_COLUMNS = `f.id, f.run_id AS "runId", f.step_id AS "stepId", f.kind, f.rating,
       f.body, u.name AS "authorName", f.learning_status AS "learningStatus",
       f.created_at AS "createdAt"`;

/**
 * Insert one feedback row as the acting user; learning_status starts at
 * "new". Validation happened in the action; the table's CHECK constraints
 * hold the same line. Returns the new row's id.
 */
export async function insertFeedback(
  orgId: string,
  authorId: string,
  input: InsertFeedbackInput,
): Promise<string> {
  return withOrgContext({ orgId, userId: authorId }, async (client) => {
    const { rows } = await client.query<{ id: string }>(
      `INSERT INTO feedback (org_id, run_id, step_id, author_id, kind, rating, body)
       VALUES ($1, $2, $3, $4, $5, $6, $7)
       RETURNING id`,
      [
        orgId,
        input.runId,
        input.stepId ?? null,
        authorId,
        input.kind,
        input.rating ?? null,
        input.body ?? null,
      ],
    );
    const id = rows[0]?.id;
    if (!id) throw new Error("feedback insert returned no row");
    return id;
  });
}

/** Feedback on one run, newest first. */
export async function listFeedbackForRun(orgId: string, runId: string): Promise<FeedbackItem[]> {
  return withOrgContext({ orgId }, async (client) => {
    const { rows } = await client.query<FeedbackItem>(
      `SELECT ${ITEM_COLUMNS}
         FROM feedback f
         JOIN users u ON u.id = f.author_id
        WHERE f.org_id = $1 AND f.run_id = $2
        ORDER BY f.created_at DESC`,
      [orgId, runId],
    );
    return rows;
  });
}

/** How routine-level reads resolve which runs belong to the routine. */
export interface RoutineFeedbackOptions {
  /** Injectable for tests and fixture-only setups; defaults to GET /runs. */
  runIdsForRoutine?: (orgId: string, routineId: string) => Promise<Set<string>>;
}

async function runIdsFromControlPlane(orgId: string, routineId: string): Promise<Set<string>> {
  const tenantId = await orgTenantId(orgId);
  const runs = await listRuns(
    { actorId: "system:feedback", tenantId },
    { agentId: routineId, limit: 200 },
  );
  return new Set(runs.map((run) => run.run_id));
}

/**
 * Feedback across all of a routine's runs, newest first. Feedback rows
 * carry run ids only (no domain mirrors); the run list's agent filter
 * resolves which runs belong to the routine.
 */
export async function listFeedbackForRoutine(
  orgId: string,
  routineId: string,
  options: RoutineFeedbackOptions = {},
): Promise<FeedbackItem[]> {
  const all = await listFeedbackForOrg(orgId, 500);
  const resolve = options.runIdsForRoutine ?? runIdsFromControlPlane;
  let runIds: Set<string>;
  try {
    runIds = await resolve(orgId, routineId);
  } catch {
    return [];
  }
  return all.filter((item) => runIds.has(item.runId));
}

/** Recent feedback across the organization, for the learning review strip. */
export async function listFeedbackForOrg(orgId: string, limit = 20): Promise<FeedbackItem[]> {
  return withOrgContext({ orgId }, async (client) => {
    const { rows } = await client.query<FeedbackItem>(
      `SELECT ${ITEM_COLUMNS}
         FROM feedback f
         JOIN users u ON u.id = f.author_id
        WHERE f.org_id = $1
        ORDER BY f.created_at DESC
        LIMIT $2`,
      [orgId, limit],
    );
    return rows;
  });
}

/** Move rows along the new -> queued -> consumed lifecycle; only the legal step is taken. */
async function transition(
  client: PoolClient,
  orgId: string,
  ids: string[],
  from: LearningStatus,
  to: LearningStatus,
): Promise<number> {
  if (ids.length === 0) return 0;
  const result = await client.query(
    `UPDATE feedback SET learning_status = $4
      WHERE org_id = $1 AND id = ANY($2) AND learning_status = $3`,
    [orgId, ids, from, to],
  );
  return result.rowCount ?? 0;
}

/**
 * The learning handoff seam: mark new feedback queued for the learning
 * service. TODO(learning): the service pulls queued rows and acknowledges
 * consumption; until then the website drives both transitions.
 */
export async function markFeedbackQueued(orgId: string, ids: string[]): Promise<number> {
  return withOrgContext({ orgId }, (client) => transition(client, orgId, ids, "new", "queued"));
}

/** Mark queued feedback consumed by a shipped change. TODO(learning). */
export async function markFeedbackConsumed(orgId: string, ids: string[]): Promise<number> {
  return withOrgContext({ orgId }, (client) => transition(client, orgId, ids, "queued", "consumed"));
}

/**
 * Queue every "new" feedback row on a routine's runs: the moment when
 * a person approves an improvement and the feedback behind it rides along
 * to learning.
 */
export async function queueRoutineFeedback(
  orgId: string,
  routineId: string,
  options: RoutineFeedbackOptions = {},
): Promise<number> {
  const items = await listFeedbackForRoutine(orgId, routineId, options);
  const ids = items.filter((item) => item.learningStatus === "new").map((item) => item.id);
  return markFeedbackQueued(orgId, ids);
}

/** Consume a routine's queued feedback when its improvement ships. */
export async function consumeRoutineFeedback(
  orgId: string,
  routineId: string,
  options: RoutineFeedbackOptions = {},
): Promise<number> {
  const items = await listFeedbackForRoutine(orgId, routineId, options);
  const ids = items.filter((item) => item.learningStatus === "queued").map((item) => item.id);
  return markFeedbackConsumed(orgId, ids);
}
