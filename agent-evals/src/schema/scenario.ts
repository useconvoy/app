/**
 * Scenario — the canonical eval-task format. Scenarios are PURE DATA (JSON
 * validated by these zod schemas): diffable, content-addressable, capturable
 * from real runs, and eventually customer-authorable. TypeScript is allowed
 * only as an authoring convenience that emits this JSON.
 *
 * A scenario bundles: world fixture + counterparty scripts + approval script +
 * budgets + sealed answer key + graders. The approval script is simultaneously
 * the unattended gate resolver AND an expected-gate assertion set.
 */

import { z } from 'zod';
import { ArgsMatcherSchema, KeyRefSchema } from './match.ts';
import { GateKindSchema } from '../runtime/events.ts';

/** Sim-time duration: "45d" | "4h" | "30m" | "10s". */
export const DurationSchema = z.string().regex(/^\d+(\.\d+)?(d|h|m|s)$/);
export type Duration = z.infer<typeof DurationSchema>;

export function parseDuration(d: Duration): number {
  const value = parseFloat(d);
  const unit = d.slice(-1);
  const ms = { d: 86_400_000, h: 3_600_000, m: 60_000, s: 1_000 }[unit];
  if (!ms) throw new Error(`bad duration: ${d}`);
  return value * ms;
}

/** Seeded delay: fixed or uniform range (resolved by the scenario PRNG). */
export const SimDelaySchema = z.union([
  DurationSchema,
  z.object({ dist: z.literal('uniform'), min: DurationSchema, max: DurationSchema }),
]);
export type SimDelay = z.infer<typeof SimDelaySchema>;

// ---------------------------------------------------------------------------
// World fixtures
// ---------------------------------------------------------------------------

export const FixtureRefSchema = z.object({
  /** Fixture-pack directory name under scenarios/packs/. */
  pack: z.string(),
  /** Content hash of the TEMPLATED pack ({{t0±Nd}} dates unmaterialized). */
  packHash: z.string(),
});

/** Per-tool binding — fidelity is chosen per tool, not globally. */
export const BindingSchema = z.union([
  z.object({ kind: z.literal('emulator') }),
  z.object({ kind: z.literal('replay'), cassette: z.string(), miss: z.enum(['fail', 'emulator', 'record']) }),
  z.object({ kind: z.literal('live-read') }),
]);
export type Binding = z.infer<typeof BindingSchema>;

// ---------------------------------------------------------------------------
// Trigger — v1 supports only 'api' and 'schedule'; the harness starts missions
// itself (inbound-message triggers need runtime watcher machinery, deferred).
// ---------------------------------------------------------------------------

const MissionSpecInputSchema = z.object({
  missionType: z.string(),
  goal: z.string(),
  params: z.record(z.string(), z.unknown()).optional(),
});

export const TriggerSchema = z.union([
  z.object({ kind: z.literal('api'), missionSpec: MissionSpecInputSchema }),
  z.object({ kind: z.literal('schedule'), atSim: DurationSchema, missionSpec: MissionSpecInputSchema }),
]);
export type Trigger = z.infer<typeof TriggerSchema>;

// ---------------------------------------------------------------------------
// Counterparties — scripted state machines; semantics always script-chosen.
// ---------------------------------------------------------------------------

export const MessageTemplateSchema = z.object({
  subject: z.string(),
  body: z.string(),
  from: z.string().optional(),
});

export const CounterpartyRuleSchema = z.object({
  id: z.string(),
  match: z.object({
    toContains: z.string().optional(),
    subjectRegex: z.string().optional(),
    bodyRegex: z.string().optional(),
    hasAttachment: z.boolean().optional(),
    /** 1-based nth matching message for this rule's actor — occurrence: 2 = "answer only the chase". */
    occurrence: z.number().int().positive().optional(),
  }),
  maxFires: z.number().int().positive().default(1),
  respond: z.array(
    z.object({
      afterSim: SimDelaySchema,
      message: MessageTemplateSchema,
      /** Paths into the fixture pack; may reference trap docs. */
      attachments: z.array(z.string()).optional(),
    })
  ),
});
export type CounterpartyRule = z.infer<typeof CounterpartyRuleSchema>;

export const CounterpartyProfileSchema = z.enum([
  'cooperative', 'slow', 'wrong-document', 'silent', 'adversarial-injection', 'custom',
]);

export const CounterpartyScriptSchema = z.object({
  actorId: z.string(),
  channel: z.enum(['email', 'portal']),
  /** Addresses / portal account ids this actor controls. */
  owns: z.array(z.string()),
  profile: CounterpartyProfileSchema,
  /** Required iff profile === 'custom'; presets expand to rules at load. */
  rules: z.array(CounterpartyRuleSchema).optional(),
  /** Params for preset expansion (e.g. which attachment set is "correct"). */
  params: z.record(z.string(), z.unknown()).optional(),
  unsolicited: z
    .array(z.object({ atSim: DurationSchema, message: MessageTemplateSchema, attachments: z.array(z.string()).optional() }))
    .optional(),
  default: z.enum(['silent', 'bounce']).default('silent'),
});
export type CounterpartyScript = z.infer<typeof CounterpartyScriptSchema>;

// ---------------------------------------------------------------------------
// Approval script — resolver + assertion in one object.
// ---------------------------------------------------------------------------

export const GateMatcherSchema = z.object({
  kind: GateKindSchema.optional(),
  stepTag: z.string().optional(),
  payload: ArgsMatcherSchema.optional(),
});
export type GateMatcher = z.infer<typeof GateMatcherSchema>;

export const GateResolutionSchema = z.union([
  z.object({ kind: z.literal('approve') }),
  z.object({ kind: z.literal('reject'), reason: z.string() }),
  z.object({ kind: z.literal('provide_input'), payload: z.unknown() }),
  z.object({ kind: z.literal('raise_budget'), addUsd: z.number().positive() }),
  z.object({ kind: z.literal('edit_then_approve'), patch: z.unknown() }),
  z.object({ kind: z.literal('expire') }),
]);
export type GateResolution = z.infer<typeof GateResolutionSchema>;

export const GateScriptStepSchema = z.object({
  id: z.string(),
  expect: GateMatcherSchema,
  resolve: GateResolutionSchema,
  /** Simulated human latency; exercises park-and-resume for free. */
  afterSim: SimDelaySchema.optional(),
  optional: z.boolean().default(false),
  /** Must fire in listed order relative to other ordered steps. */
  ordered: z.boolean().default(true),
  maxFires: z.number().int().positive().default(1),
});
export type GateScriptStep = z.infer<typeof GateScriptStepSchema>;

export const ApprovalScriptSchema = z.union([
  z.object({ mode: z.literal('auto_approve'), maxGates: z.number().int().positive(), afterSim: SimDelaySchema.optional() }),
  z.object({ mode: z.literal('auto_reject'), reason: z.string() }),
  z.object({
    mode: z.literal('scripted'),
    steps: z.array(GateScriptStepSchema),
    onUnexpectedGate: z
      .union([z.literal('fail_scenario'), z.object({ resolve: GateResolutionSchema })])
      .default('fail_scenario'),
  }),
]);
export type ApprovalScript = z.infer<typeof ApprovalScriptSchema>;

// ---------------------------------------------------------------------------
// Budgets
// ---------------------------------------------------------------------------

export const BudgetsSchema = z.object({
  usd: z.number().positive(),
  tokens: z.number().int().positive().optional(),
  /** Mission deadline on the sim clock. */
  simTime: DurationSchema,
  /** Harness kill switch — catches spins the sim clock can't see. */
  wallClock: DurationSchema,
  onExhaustion: z.enum(['fail', 'grade_partial']).default('fail'),
});
export type Budgets = z.infer<typeof BudgetsSchema>;

// ---------------------------------------------------------------------------
// Graders
// ---------------------------------------------------------------------------

const CmpSchema = z.enum(['eq', 'neq', 'lt', 'lte', 'gt', 'gte', 'contains', 'regex', 'in', 'exists', 'absent']);
const ExpectedSchema = z.union([KeyRefSchema, z.unknown()]);

export const ArtifactSelectorSchema = z.object({
  /** Tag pattern; "{itemRef}" interpolates the item's domain key. */
  tag: z.string().optional(),
  mime: z.string().optional(),
  minBytes: z.number().int().positive().optional(),
});

export const FieldExtractorSchema = z.union([
  z.object({ kind: z.literal('json_path'), path: z.string() }),
  z.object({ kind: z.literal('regex'), pattern: z.string(), group: z.number().int().nonnegative() }),
]);

export const EndStateAssertionSchema = z.union([
  z.object({ kind: z.literal('artifact_exists'), selector: ArtifactSelectorSchema }),
  z.object({
    kind: z.literal('artifact_field'),
    selector: ArtifactSelectorSchema,
    extract: FieldExtractorSchema,
    op: CmpSchema,
    expected: ExpectedSchema.optional(),
  }),
  z.object({
    kind: z.literal('world_query'),
    /** e.g. "records.policy.POL-1042.renewal_status" | "messages.sent.*.to" */
    query: z.string(),
    op: CmpSchema,
    expected: ExpectedSchema.optional(),
  }),
  /** Expands the answer key's checklist into one weighted sub-assertion per check. */
  z.object({ kind: z.literal('checklist'), checklistRef: z.string() }),
]);
export type EndStateAssertion = z.infer<typeof EndStateAssertionSchema>;

export const EventMatcherSchema = z.object({
  type: z.union([z.string(), z.array(z.string())]).optional(),
  tool: z.string().optional(),
  args: ArgsMatcherSchema.optional(),
  itemRef: z.string().optional(),
});
export type EventMatcher = z.infer<typeof EventMatcherSchema>;

export const TrajectoryAssertionSchema = z.union([
  z.object({ kind: z.literal('never'), where: EventMatcherSchema }),
  z.object({ kind: z.literal('always'), where: EventMatcherSchema, require: ArgsMatcherSchema }),
  z.object({ kind: z.literal('event_count'), where: EventMatcherSchema, op: CmpSchema, n: z.number().int().nonnegative() }),
  z.object({ kind: z.literal('sequence'), steps: z.array(EventMatcherSchema), scope: EventMatcherSchema.optional() }),
  z.object({ kind: z.literal('paused_at_gate'), gate: GateMatcherSchema, effect: EventMatcherSchema }),
  z.object({ kind: z.literal('eventually'), where: EventMatcherSchema, withinSim: DurationSchema.optional() }),
  z.object({
    kind: z.literal('budget_shape'),
    metric: z.enum(['usd', 'tokens', 'tool_calls']),
    op: CmpSchema,
    limit: z.number().nonnegative(),
    per: z.enum(['run', 'item']).default('run'),
  }),
]);
export type TrajectoryAssertion = z.infer<typeof TrajectoryAssertionSchema>;

export const JudgeInputSchema = z.union([
  z.object({ kind: z.literal('artifact'), selector: ArtifactSelectorSchema }),
  z.object({ kind: z.literal('key_excerpt'), ref: KeyRefSchema }),
  z.object({ kind: z.literal('world'), query: z.string() }),
  // Deliberately NO constructor for transcript, messages, plan text, or
  // model_call events: the worker's reasoning cannot leak into the judge
  // because the type system cannot express it.
]);
export type JudgeInput = z.infer<typeof JudgeInputSchema>;

const graderBase = {
  id: z.string(),
  scope: z.enum(['run', 'item']),
  weight: z.number().positive().default(1),
  /** invariant: must hold in EVERY trial. quality: goes through pass rates. */
  class: z.enum(['invariant', 'quality']),
};

export const GraderSpecSchema = z.discriminatedUnion('grader', [
  z.object({ ...graderBase, grader: z.literal('end_state'), asserts: z.array(EndStateAssertionSchema) }),
  z.object({ ...graderBase, grader: z.literal('trajectory'), asserts: z.array(TrajectoryAssertionSchema) }),
  z.object({
    ...graderBase,
    grader: z.literal('judge'),
    judgeId: z.string(),
    model: z.string(),
    promptFile: z.string(),
    promptHash: z.string(),
    rubric: z.array(z.object({ id: z.string(), criterion: z.string(), weight: z.number().positive() })),
    inputs: z.array(JudgeInputSchema),
    samples: z.number().int().positive().default(3),
    passAt: z.number().min(0).max(1),
  }),
  z.object({ ...graderBase, grader: z.literal('probe'), probeId: z.string() }),
  /** Escape hatch: allowed, discouraged, hash-pinned. */
  z.object({ ...graderBase, grader: z.literal('ts'), module: z.string(), moduleHash: z.string() }),
]);
export type GraderSpec = z.infer<typeof GraderSpecSchema>;

// ---------------------------------------------------------------------------
// Probes — injected through counterparty channels, never in harness voice.
// ---------------------------------------------------------------------------

export const ProbeSpecSchema = z.union([
  z.object({
    kind: z.literal('criteria_retention'),
    probeId: z.string(),
    atItemOrdinal: z.number().int().positive(),
    via: z.object({ actorId: z.string(), message: MessageTemplateSchema }),
    expectReply: z.object({ matcher: ArgsMatcherSchema }),
  }),
  z.object({
    kind: z.literal('injection_resistance'),
    probeId: z.string(),
    payload: z.string(),
    mustNot: z.array(TrajectoryAssertionSchema),
    mustStill: z.array(EndStateAssertionSchema),
  }),
]);
export type ProbeSpec = z.infer<typeof ProbeSpecSchema>;

// ---------------------------------------------------------------------------
// Trials & items
// ---------------------------------------------------------------------------

export const TrialPolicySchema = z.union([
  z.object({ n: z.number().int().positive(), passRule: z.object({ kind: z.literal('at_least'), k: z.number().int().positive() }) }),
  z.object({
    n: z.number().int().positive(),
    passRule: z.object({
      kind: z.literal('aggregate'),
      /** null → use the eval-set config thresholds (the ONE place they live). */
      thresholdsRef: z.literal('eval-set').default('eval-set'),
    }),
  }),
]);
export type TrialPolicy = z.infer<typeof TrialPolicySchema>;

export const ItemSpecSchema = z.object({
  itemId: z.string(),
  /** n in Q(n): the order the environment presents work. */
  ordinal: z.number().int().positive(),
  /** Subtree of answerKey.perItem. */
  keyRef: z.string(),
  graders: z.union([z.array(GraderSpecSchema), z.literal('inherit')]),
});
export type ItemSpec = z.infer<typeof ItemSpecSchema>;

// ---------------------------------------------------------------------------
// Answer key (sealed, content-addressed, never reachable from the environment)
// ---------------------------------------------------------------------------

export const AnswerKeySchema = z.object({
  scenarioId: z.string(),
  facts: z.record(z.string(), z.unknown()),
  perItem: z.record(z.string(), z.record(z.string(), z.unknown())).optional(),
  checklists: z
    .record(
      z.string(),
      z.array(z.object({ id: z.string(), description: z.string(), assert: EndStateAssertionSchema, weight: z.number().positive().default(1) }))
    )
    .optional(),
  rubricExcerpts: z.record(z.string(), z.string()).optional(),
});
export type AnswerKey = z.infer<typeof AnswerKeySchema>;

// ---------------------------------------------------------------------------
// Scenario + eval set
// ---------------------------------------------------------------------------

export const ScenarioSchema = z.object({
  id: z.string(),
  title: z.string(),
  missionType: z.string(),
  kind: z.enum(['single', 'gauntlet']),
  fixture: FixtureRefSchema,
  /** Sim epoch, ISO date; choose near real today so the model's date prior doesn't fight it. */
  t0: z.string(),
  /** One PRNG seed for every stochastic choice in the scenario. */
  seed: z.number().int(),
  bindings: z.record(z.string(), BindingSchema).default({}),
  trigger: TriggerSchema,
  counterparties: z.array(CounterpartyScriptSchema),
  approvals: ApprovalScriptSchema,
  budgets: BudgetsSchema,
  answerKeyRef: z.object({ path: z.string(), hash: z.string() }),
  graders: z.array(GraderSpecSchema),
  items: z.array(ItemSpecSchema).optional(),
  /** Per-item grader template, applied to each item with graders:'inherit'. */
  itemGraderTemplate: z.array(GraderSpecSchema).optional(),
  probes: z.array(ProbeSpecSchema).optional(),
  trials: TrialPolicySchema.optional(),
  provenance: z.union([
    z.object({ kind: z.literal('authored') }),
    z.object({ kind: z.literal('captured'), runId: z.string(), redacted: z.boolean() }),
  ]),
  tags: z.array(z.string()).default([]),
});
export type Scenario = z.infer<typeof ScenarioSchema>;

/** Decay thresholds live HERE and only here — fixed ex-ante, versioned. */
export const EvalSetConfigSchema = z.object({
  name: z.string(),
  version: z.string(),
  scenarios: z.array(z.string()),
  thresholds: z.object({
    slopeMin: z.number(),
    aucMin: z.number().min(0).max(1),
    itemFloor: z.number().min(0).max(1),
  }),
  defaultTrials: z.object({ single: TrialPolicySchema, gauntlet: TrialPolicySchema }),
  costRegressionGuardPct: z.number().positive().default(25),
});
export type EvalSetConfig = z.infer<typeof EvalSetConfigSchema>;
