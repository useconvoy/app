/**
 * Verdict — the leaf grading record — plus the pure aggregation layers above
 * it (ItemVerdict, TrialResult, ScenarioVerdict, SuiteResult).
 *
 * Contracts encoded here:
 *  - status 'fail' MUST carry >= 1 evidence (enforced by zod refinement);
 *  - 'error' is a harness/grader failure, never a subject failure, and is
 *    never green;
 *  - invariant-class verdicts must pass in EVERY trial — one violation
 *    anywhere is red, pass rates can never launder it away.
 */

import { z } from 'zod';

export const EvidenceSchema = z.union([
  z.object({ kind: z.literal('event'), eventId: z.string(), note: z.string().optional() }),
  z.object({ kind: z.literal('artifact'), hash: z.string(), excerpt: z.string().optional() }),
  z.object({ kind: z.literal('world'), query: z.string(), result: z.unknown() }),
  z.object({ kind: z.literal('judge_rationale'), sampleIdx: z.number().int().nonnegative(), text: z.string() }),
  z.object({ kind: z.literal('note'), text: z.string() }),
]);
export type Evidence = z.infer<typeof EvidenceSchema>;

export const VerdictSchema = z
  .object({
    graderId: z.string(),
    /** Content hash of the grader spec (+ prompt file for judges) — makes taint a query. */
    graderVersion: z.string(),
    class: z.enum(['invariant', 'quality']),
    scope: z.object({ runId: z.string(), itemId: z.string().optional() }),
    status: z.enum(['pass', 'fail', 'error', 'skipped', 'missing']),
    /** 0..1; deterministic graders emit 0|1 unless asserts are weighted. */
    score: z.number().min(0).max(1),
    evidence: z.array(EvidenceSchema),
    costUsd: z.number().nonnegative().optional(),
    /** Judge sample split (e.g. 2-1 majority). */
    lowConfidence: z.boolean().optional(),
    /** True while the judge version is uncalibrated: reported, never gating. */
    advisory: z.boolean().optional(),
  })
  .refine((v) => v.status !== 'fail' || v.evidence.length >= 1, {
    message: 'fail verdicts must carry at least one piece of evidence',
  });
export type Verdict = z.infer<typeof VerdictSchema>;

export const ItemVerdictSchema = z.object({
  itemId: z.string(),
  ordinal: z.number().int().positive(),
  /** Weighted mean of item-scoped, non-advisory verdict scores; missing → 0. */
  q: z.number().min(0).max(1),
  status: z.enum(['pass', 'fail', 'missing', 'error']),
  verdicts: z.array(VerdictSchema),
  costUsd: z.number().nonnegative().optional(),
});
export type ItemVerdict = z.infer<typeof ItemVerdictSchema>;

export const DecayStatsSchema = z.object({
  /** OLS slope of Q vs ordinal. */
  slope: z.number(),
  /** Mean Q (area under the curve on the unit-spaced grid). */
  auc: z.number().min(0).max(1),
  firstDecileMean: z.number().min(0).max(1),
  lastDecileMean: z.number().min(0).max(1),
  itemCount: z.number().int().nonnegative(),
  minQ: z.number().min(0).max(1),
});
export type DecayStats = z.infer<typeof DecayStatsSchema>;

/** One trial = one full execution of the scenario. */
export const TrialResultSchema = z.object({
  trialIdx: z.number().int().nonnegative(),
  runId: z.string(),
  status: z.enum([
    'completed',       // ran to terminal; verdicts tell the story
    'deadlock',        // nothing will ever wake this mission — a graded outcome
    'guard_tripped',   // wall-clock / USD circuit breaker
    'budget_exceeded', // mission budget exhausted with onExhaustion: 'fail'
    'harness_error',
  ]),
  verdicts: z.array(VerdictSchema),
  items: z.array(ItemVerdictSchema),
  decay: DecayStatsSchema.nullable(),
  costUsd: z.number().nonnegative(),
  simDays: z.number().nonnegative(),
  wallMs: z.number().nonnegative(),
  deadlockDiagnosis: z.string().optional(),
});
export type TrialResult = z.infer<typeof TrialResultSchema>;

export const ScenarioVerdictSchema = z.object({
  scenarioId: z.string(),
  status: z.enum(['passed', 'failed', 'error', 'quarantined', 'skipped_budget']),
  /** Any invariant-class failure in any trial forces failed + this flag. */
  invariantViolation: z.boolean(),
  trials: z.array(TrialResultSchema),
  /** Mean Q(n) across trials, per ordinal — the curve the chart plots. */
  meanDecay: DecayStatsSchema.nullable(),
  passDetail: z.string(),
  totalCostUsd: z.number().nonnegative(),
});
export type ScenarioVerdict = z.infer<typeof ScenarioVerdictSchema>;

export const SuiteResultSchema = z.object({
  evalSet: z.object({ name: z.string(), version: z.string() }),
  subject: z.object({
    kind: z.enum(['scripted', 'baseline', 'runtime']),
    label: z.string(),
    modelId: z.string().optional(),
  }),
  startedAt: z.string(),
  finishedAt: z.string(),
  scenarios: z.array(ScenarioVerdictSchema),
  green: z.boolean(),
  greenDetail: z.array(z.string()),
  totalCostUsd: z.number().nonnegative(),
});
export type SuiteResult = z.infer<typeof SuiteResultSchema>;
