/**
 * The event taxonomy — the append-only per-mission log every part of Convoy
 * reads. This file is the agent-evals copy of the co-signed schema; graders,
 * telemetry views, and learning jobs are all readers of exactly this shape.
 *
 * Every event carries two timestamps:
 *   ts     — DOMAIN time, from ClockPort (virtualized in the sandbox)
 *   wallTs — MECHANICAL wall-clock time (latency/cost telemetry; never graded)
 *
 * Two-phase side effects: effectful tools emit intent → approved → executed →
 * result. Pure/read tools emit a single collapsed `tool_call` event (the
 * effect-class rule: the 4-event envelope on reads buys nothing).
 */

import { z } from 'zod';

export type MissionId = string;
export type GateId = string;
export type EventId = string;

export const GateKindSchema = z.enum([
  'action-approval',
  'input-request',
  'plan-approval',
  'budget-raise',
]);
export type GateKind = z.infer<typeof GateKindSchema>;

export const GateResolutionKindSchema = z.enum([
  'approve',
  'reject',
  'provide_input',
  'raise_budget',
  'edit_then_approve',
  'expire',
]);

const iso = z.string(); // ISO-8601 datetime strings

/** Fields common to every event row. */
const base = {
  eventId: z.string(),
  missionId: z.string(),
  /** Per-mission monotonic sequence; ties on `ts` resolve by `seq`. */
  seq: z.number().int().nonnegative(),
  ts: iso,
  wallTs: iso,
  /** Gauntlet item attribution by domain key, e.g. "packet/POL-1042". */
  itemRef: z.string().optional(),
  stepId: z.string().optional(),
  attemptId: z.string().optional(),
};

export const EventSchema = z.discriminatedUnion('type', [
  z.object({
    ...base,
    type: z.literal('mission_started'),
    missionType: z.string(),
    environmentId: z.string(),
    goal: z.string(),
    /** Immutable spec snapshot rider: model + prompt hashes for the certified tuple. */
    spec: z.object({
      modelId: z.string(),
      promptHashes: z.record(z.string(), z.string()),
      policyHash: z.string().optional(),
      toolManifestHash: z.string().optional(),
      params: z.record(z.string(), z.unknown()).optional(),
    }),
  }),
  z.object({
    ...base,
    type: z.literal('plan_version'),
    version: z.number().int().positive(),
    plan: z.unknown(),
    diff: z.unknown().optional(),
    author: z.enum(['agent', 'human']),
    causeEventId: z.string().nullable(),
  }),
  z.object({ ...base, type: z.literal('step_started'), goal: z.string().optional() }),
  z.object({
    ...base,
    type: z.literal('step_completed'),
    status: z.enum(['done', 'failed', 'skipped']),
    memo: z.string().optional(),
  }),
  z.object({
    ...base,
    type: z.literal('model_call'),
    modelId: z.string(),
    tokensIn: z.number().int().nonnegative(),
    tokensOut: z.number().int().nonnegative(),
    costUsd: z.number().nonnegative(),
    /** Exact list of what was assembled into the prompt (the week-1 rider). */
    contextManifest: z.array(z.string()).optional(),
  }),
  /** Collapsed event for pure/read tools. */
  z.object({
    ...base,
    type: z.literal('tool_call'),
    tool: z.string(),
    args: z.unknown(),
    result: z.unknown().optional(),
    error: z.string().optional(),
  }),
  /** Two-phase envelope for effectful tools. */
  z.object({ ...base, type: z.literal('tool_intent'), tool: z.string(), args: z.unknown(), idempotencyKey: z.string() }),
  z.object({ ...base, type: z.literal('tool_approved'), tool: z.string(), idempotencyKey: z.string(), gateId: z.string().optional() }),
  z.object({ ...base, type: z.literal('tool_executed'), tool: z.string(), idempotencyKey: z.string(), args: z.unknown() }),
  z.object({ ...base, type: z.literal('tool_result'), tool: z.string(), idempotencyKey: z.string(), result: z.unknown().optional(), error: z.string().optional() }),
  z.object({ ...base, type: z.literal('tool_denied'), tool: z.string(), args: z.unknown().optional(), reason: z.string() }),
  z.object({
    ...base,
    type: z.literal('gate_raised'),
    gateId: z.string(),
    kind: GateKindSchema,
    payload: z.unknown(),
    deadlineAt: iso.nullable(),
    stepTag: z.string().optional(),
  }),
  z.object({
    ...base,
    type: z.literal('gate_resolved'),
    gateId: z.string(),
    resolution: GateResolutionKindSchema,
    resolvedBy: z.string(),
    reason: z.string().optional(),
    patch: z.unknown().optional(),
  }),
  z.object({ ...base, type: z.literal('steer'), message: z.string(), priority: z.enum(['advisory', 'directive']), author: z.string() }),
  z.object({
    ...base,
    type: z.literal('budget_debit'),
    usd: z.number().nonnegative(),
    tokens: z.number().int().nonnegative().optional(),
    resource: z.enum(['model', 'tool', 'other']),
  }),
  z.object({
    ...base,
    type: z.literal('human_intervention'),
    kind: z.enum(['gate_resolution', 'verdict_override', 'steer', 'plan_edit']),
    reason: z.string().optional(),
    before: z.unknown().optional(),
    after: z.unknown().optional(),
  }),
  z.object({
    ...base,
    type: z.literal('artifact_created'),
    artifactId: z.string(),
    hash: z.string(),
    tag: z.string(),
    mime: z.string().optional(),
    bytes: z.number().int().nonnegative().optional(),
  }),
  z.object({ ...base, type: z.literal('timer_scheduled'), timerId: z.string(), fireAt: iso, note: z.string().optional() }),
  z.object({ ...base, type: z.literal('timer_fired'), timerId: z.string() }),
  z.object({
    ...base,
    type: z.literal('terminal_outcome'),
    status: z.enum(['landed', 'cancelled', 'failed', 'budget_exhausted']),
    judgedBy: z.enum(['human', 'agent', 'timeout', 'harness']),
    summary: z.string().optional(),
  }),
]);

export type ConvoyEvent = z.infer<typeof EventSchema>;
export type EventType = ConvoyEvent['type'];
export type EventOfType<T extends EventType> = Extract<ConvoyEvent, { type: T }>;

/** Payload supplied by an emitter; ids/seq/timestamps are stamped by the log. */
export type EventInput = ConvoyEvent extends infer E
  ? E extends ConvoyEvent
    ? Omit<E, 'eventId' | 'seq' | 'ts' | 'wallTs'> & { ts?: string }
    : never
  : never;
