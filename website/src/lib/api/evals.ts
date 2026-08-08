/**
 * Reads and appends for routine test scores. The full evaluation service
 * (scenario suites, per-run scorecards, scored-run trajectories) is not
 * built yet; what exists today is the append-only routine_eval_scores
 * table, read through withOrgContext so RLS bounds every query. A fresh
 * organization has no scores and every consuming surface renders an
 * honest empty state.
 *
 * TODO(evals): the service will own scorecards and trajectories; their
 * types stay here so the components that render them keep one home.
 */
import "server-only";

import { withOrgContext } from "@/lib/db";

/** A scenario suite attached to one routine; service-side, not stored here. */
export interface EvalSuite {
  id: string;
  routineId: string;
  name: string;
  scenarioCount: number;
}

/** One scored run on a routine's trend line; score is 0..100. */
export interface TestScorePoint {
  at: string;
  score: number;
}

/** One scored run with its reference, for the scored-run history list. */
export interface ScoredRun {
  runRef: string;
  score: number;
  recordedAt: string;
}

/** Per-run scorecard: the criteria behind one run's test score. */
export interface RunScorecard {
  runId: string;
  score: number;
  criteria: Array<{ name: string; pass: boolean; note: string }>;
}

/** One scored run in a routine's history; rehearsals carry sandbox: true. */
export interface Trajectory {
  runId: string;
  at: string;
  sandbox: boolean;
  headline: string;
}

/** The score trend for one routine, oldest first, ready to chart. */
export async function scoreTrend(orgId: string, routineId: string): Promise<TestScorePoint[]> {
  return withOrgContext({ orgId }, async (client) => {
    const { rows } = await client.query<{ score: number; recordedAt: Date }>(
      `SELECT score, recorded_at AS "recordedAt"
         FROM routine_eval_scores
        WHERE org_id = $1 AND routine_id = $2
        ORDER BY recorded_at, id`,
      [orgId, routineId],
    );
    return rows.map((row) => ({ at: row.recordedAt.toISOString(), score: row.score }));
  });
}

/** Recent scored runs for one routine, newest first. */
export async function scoredRuns(
  orgId: string,
  routineId: string,
  limit = 50,
): Promise<ScoredRun[]> {
  return withOrgContext({ orgId }, async (client) => {
    const { rows } = await client.query<{ runRef: string; score: number; recordedAt: Date }>(
      `SELECT run_ref AS "runRef", score, recorded_at AS "recordedAt"
         FROM routine_eval_scores
        WHERE org_id = $1 AND routine_id = $2
        ORDER BY recorded_at DESC, id DESC
        LIMIT $3`,
      [orgId, routineId, limit],
    );
    return rows.map((row) => ({
      runRef: row.runRef,
      score: row.score,
      recordedAt: row.recordedAt.toISOString(),
    }));
  });
}

/**
 * Append one score to a routine's history. The history is append-only:
 * a re-scored run gets a new row, and the trend shows what was known
 * when. Rows stay narrow by design; anything wide belongs to the
 * evaluation service when it lands.
 */
export async function recordScore(
  orgId: string,
  routineId: string,
  runRef: string,
  score: number,
): Promise<void> {
  await withOrgContext({ orgId }, (client) =>
    client.query(
      `INSERT INTO routine_eval_scores (org_id, routine_id, run_ref, score)
       VALUES ($1, $2, $3, $4)`,
      [orgId, routineId, runRef, score],
    ),
  );
}
