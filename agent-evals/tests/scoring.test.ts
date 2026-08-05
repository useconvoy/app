/**
 * Scoring-layer tests: item aggregation (weighted q, missing items), decay
 * stats on a hand-made sagging curve, trial status mapping, the invariant
 * rule (one violation in any trial is red), aggregate thresholds, and
 * suiteGreen blocking on error verdicts / cost regression.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  buildItemVerdicts,
  buildScenarioVerdict,
  buildSuiteResult,
  buildTrialResult,
  decayStats,
  meanDecayAcrossTrials,
  suiteGreen,
  trialStatusFromReport,
} from '../src/scoring/index.ts';
import type { EvalSetConfig, GraderSpec, ItemSpec, Scenario } from '../src/schema/scenario.ts';
import type { ScenarioVerdict, TrialResult, Verdict } from '../src/schema/verdict.ts';
import { SuiteResultSchema } from '../src/schema/verdict.ts';
import { makeScenario } from './graders.fixtures.ts';

function v(partial: Partial<Verdict> & { graderId: string }): Verdict {
  const base: Verdict = {
    graderId: partial.graderId,
    graderVersion: 'gv',
    class: 'quality',
    scope: { runId: 'r1' },
    status: 'pass',
    score: 1,
    evidence: [],
  };
  const out = { ...base, ...partial };
  if (out.status === 'fail' && out.evidence.length === 0) {
    out.evidence = [{ kind: 'note', text: 'violated' }];
  }
  return out;
}

function tmpl(id: string, weight: number): GraderSpec {
  return {
    id,
    scope: 'item',
    weight,
    class: 'quality',
    grader: 'end_state',
    asserts: [{ kind: 'artifact_exists', selector: { tag: `x/${id}` } }],
  };
}

function items(n: number): ItemSpec[] {
  return Array.from({ length: n }, (_, i) => ({
    itemId: `POL-${i + 1}`,
    ordinal: i + 1,
    keyRef: `POL-${i + 1}`,
    graders: 'inherit' as const,
  }));
}

function gauntlet(n: number, over: Partial<Scenario> = {}): Scenario {
  return makeScenario({
    kind: 'gauntlet',
    items: items(n),
    itemGraderTemplate: [tmpl('g-a', 3), tmpl('g-b', 1)],
    ...over,
  });
}

const config: EvalSetConfig = {
  name: 'convoy-core',
  version: '1.0.0',
  scenarios: [],
  thresholds: { slopeMin: -0.02, aucMin: 0.7, itemFloor: 0.3 },
  defaultTrials: {
    single: { n: 3, passRule: { kind: 'at_least', k: 2 } },
    gauntlet: { n: 3, passRule: { kind: 'aggregate', thresholdsRef: 'eval-set' } },
  },
  costRegressionGuardPct: 25,
};

// ---------------------------------------------------------------------------
// buildItemVerdicts
// ---------------------------------------------------------------------------

test('buildItemVerdicts: weighted q, advisory excluded, untouched item -> q 0 missing', () => {
  const scenario = gauntlet(3);
  const verdicts: Verdict[] = [
    // POL-1: both pass; an advisory judge fail must not gate or drag q.
    v({ graderId: 'g-a', scope: { runId: 'r1', itemId: 'POL-1' } }),
    v({ graderId: 'g-b', scope: { runId: 'r1', itemId: 'POL-1' } }),
    v({ graderId: 'judge-1', scope: { runId: 'r1', itemId: 'POL-1' }, status: 'fail', score: 0, advisory: true }),
    // POL-2: weight-3 grader passes, weight-1 fails -> q = 3/4.
    v({ graderId: 'g-a', scope: { runId: 'r1', itemId: 'POL-2' } }),
    v({ graderId: 'g-b', scope: { runId: 'r1', itemId: 'POL-2' }, status: 'fail', score: 0 }),
    // POL-3: nothing — the agent never touched it.
  ];
  const iv = buildItemVerdicts(scenario, verdicts);
  assert.equal(iv.length, 3);

  assert.equal(iv[0]!.itemId, 'POL-1');
  assert.equal(iv[0]!.status, 'pass');
  assert.equal(iv[0]!.q, 1);

  assert.equal(iv[1]!.status, 'fail');
  assert.equal(iv[1]!.q, 0.75);

  assert.equal(iv[2]!.itemId, 'POL-3');
  assert.equal(iv[2]!.status, 'missing');
  assert.equal(iv[2]!.q, 0);
  assert.equal(iv[2]!.verdicts.length, 0);
});

test('buildItemVerdicts: an error verdict marks the item error', () => {
  const scenario = gauntlet(1);
  const iv = buildItemVerdicts(scenario, [
    v({ graderId: 'g-a', scope: { runId: 'r1', itemId: 'POL-1' }, status: 'error', score: 0 }),
  ]);
  assert.equal(iv[0]!.status, 'error');
});

// ---------------------------------------------------------------------------
// decayStats
// ---------------------------------------------------------------------------

test('decayStats on a hand-made sagging curve: negative slope, correct deciles', () => {
  // q(n) = 1 - 0.04*(n-1) over 20 items: 1.00 down to 0.24, slope exactly -0.04.
  const pts = Array.from({ length: 20 }, (_, i) => ({ ordinal: i + 1, q: 1 - 0.04 * i }));
  const d = decayStats(pts);
  const close = (a: number, b: number) => assert.ok(Math.abs(a - b) < 1e-9, `${a} !~ ${b}`);
  close(d.slope, -0.04);
  assert.ok(d.slope < 0);
  close(d.auc, (1 + 0.24) / 2); // mean of a linear ramp
  assert.equal(d.itemCount, 20);
  close(d.firstDecileMean, (1 + 0.96) / 2); // ceil(20/10) = 2 items
  close(d.lastDecileMean, (0.28 + 0.24) / 2);
  close(d.minQ, 0.24);
});

test('decayStats handles flat and tiny inputs', () => {
  const flat = decayStats([
    { ordinal: 1, q: 0.9 },
    { ordinal: 2, q: 0.9 },
  ]);
  assert.equal(flat.slope, 0);
  assert.equal(flat.auc, 0.9);

  const single = decayStats([{ ordinal: 1, q: 0.7 }]);
  assert.equal(single.slope, 0);
  assert.equal(single.firstDecileMean, 0.7);
  assert.equal(single.lastDecileMean, 0.7);

  const empty = decayStats([]);
  assert.equal(empty.itemCount, 0);
});

// ---------------------------------------------------------------------------
// buildTrialResult — status mapping
// ---------------------------------------------------------------------------

test('buildTrialResult maps run reports to trial statuses', () => {
  const scenario = makeScenario(); // onExhaustion: 'fail'
  const mk = (report: Parameters<typeof trialStatusFromReport>[0]) =>
    buildTrialResult(0, 'r1', report, [], scenario, 1, 45, 1000).status;

  assert.equal(mk({}), 'completed');
  assert.equal(mk({ terminal: 'landed' }), 'completed');
  assert.equal(mk({ deadlock: true }), 'deadlock');
  assert.equal(mk({ guardTripped: 'wall' }), 'guard_tripped');
  assert.equal(mk({ guardTripped: 'usd' }), 'budget_exceeded'); // onExhaustion 'fail'

  const gradePartial = makeScenario({
    budgets: { usd: 10, simTime: '45d', wallClock: '10m', onExhaustion: 'grade_partial' },
  });
  assert.equal(
    buildTrialResult(0, 'r1', { guardTripped: 'usd' }, [], gradePartial, 1, 45, 1000).status,
    'guard_tripped'
  );
});

test('buildTrialResult object form carries a precomputed status and diagnosis', () => {
  const trial = buildTrialResult({
    scenario: makeScenario(),
    trialIdx: 2,
    runId: 'r2',
    status: 'deadlock',
    deadlockDiagnosis: 'no timers, no gates, not terminal',
    verdicts: [],
    costUsd: 0.5,
    simDays: 3,
    wallMs: 100,
  });
  assert.equal(trial.status, 'deadlock');
  assert.equal(trial.deadlockDiagnosis, 'no timers, no gates, not terminal');
  assert.equal(trial.decay, null); // single scenario, no items
});

// ---------------------------------------------------------------------------
// buildScenarioVerdict — invariant rule, at_least, aggregate
// ---------------------------------------------------------------------------

function qualityTrial(scenario: Scenario, idx: number, verdicts: Verdict[], status: TrialResult['status'] = 'completed'): TrialResult {
  return buildTrialResult({
    scenario,
    trialIdx: idx,
    runId: `r${idx}`,
    status,
    verdicts,
    costUsd: 1,
    simDays: 45,
    wallMs: 1000,
  });
}

test('INVARIANT RULE: one invariant fail in trial 2 of 3 turns the scenario red despite at_least k=2', () => {
  const scenario = makeScenario({ trials: { n: 3, passRule: { kind: 'at_least', k: 2 } } });
  const passing = () => [v({ graderId: 'q1' })];
  const trials = [
    qualityTrial(scenario, 0, passing()),
    qualityTrial(scenario, 1, [
      ...passing(),
      v({ graderId: 'two-phase', class: 'invariant', status: 'fail', score: 0 }),
    ]),
    qualityTrial(scenario, 2, passing()),
  ];
  const verdict = buildScenarioVerdict(scenario, trials, config);
  assert.equal(verdict.invariantViolation, true);
  assert.equal(verdict.status, 'failed');

  // Sanity: without the invariant verdict the same trials pass at_least k=2.
  const cleanTrials = [
    qualityTrial(scenario, 0, passing()),
    qualityTrial(scenario, 1, passing()),
    qualityTrial(scenario, 2, passing()),
  ];
  const clean = buildScenarioVerdict(scenario, cleanTrials, config);
  assert.equal(clean.status, 'passed');
  assert.equal(clean.invariantViolation, false);
});

test('at_least counts only completed trials whose non-advisory quality verdicts all pass', () => {
  const scenario = makeScenario({ trials: { n: 3, passRule: { kind: 'at_least', k: 2 } } });
  const trials = [
    qualityTrial(scenario, 0, [v({ graderId: 'q1' })]),
    qualityTrial(scenario, 1, [v({ graderId: 'q1', status: 'fail', score: 0 })]),
    qualityTrial(scenario, 2, [v({ graderId: 'q1' })], 'deadlock'), // completed only
  ];
  const verdict = buildScenarioVerdict(scenario, trials, config);
  assert.equal(verdict.status, 'failed'); // only 1 qualifying trial

  const advisoryOnlyFail = [
    qualityTrial(scenario, 0, [v({ graderId: 'q1' })]),
    qualityTrial(scenario, 1, [
      v({ graderId: 'q1' }),
      v({ graderId: 'judge', status: 'fail', score: 0, advisory: true }),
    ]),
    qualityTrial(scenario, 2, [v({ graderId: 'q1' })]),
  ];
  assert.equal(buildScenarioVerdict(scenario, advisoryOnlyFail, config).status, 'passed');
});

function gauntletTrial(scenario: Scenario, idx: number, qByOrdinal: number[]): TrialResult {
  const verdicts = qByOrdinal.flatMap((q, i) => [
    v({ graderId: 'g-a', scope: { runId: `r${idx}`, itemId: `POL-${i + 1}` }, score: q }),
    v({ graderId: 'g-b', scope: { runId: `r${idx}`, itemId: `POL-${i + 1}` }, score: q }),
  ]);
  return qualityTrial(scenario, idx, verdicts);
}

test('aggregate rule passes healthy decay and fails a sagging curve on thresholds', () => {
  const scenario = gauntlet(10, { trials: { n: 2, passRule: { kind: 'aggregate', thresholdsRef: 'eval-set' } } });

  const healthyQ = Array.from({ length: 10 }, () => 0.9);
  const healthy = buildScenarioVerdict(
    scenario,
    [gauntletTrial(scenario, 0, healthyQ), gauntletTrial(scenario, 1, healthyQ)],
    config
  );
  assert.equal(healthy.status, 'passed');
  assert.ok(healthy.meanDecay);
  assert.ok(Math.abs(healthy.meanDecay!.auc - 0.9) < 1e-9);

  // Sagging: slope -0.08 < slopeMin -0.02, last items under the 0.3 floor.
  const saggingQ = Array.from({ length: 10 }, (_, i) => 1 - 0.08 * i);
  const sagging = buildScenarioVerdict(
    scenario,
    [gauntletTrial(scenario, 0, saggingQ), gauntletTrial(scenario, 1, saggingQ)],
    config
  );
  assert.equal(sagging.status, 'failed');
  assert.ok(sagging.meanDecay!.slope < config.thresholds.slopeMin);
  assert.ok(sagging.passDetail.includes('aggregate'));
});

test('meanDecayAcrossTrials averages q per ordinal across trials', () => {
  const scenario = gauntlet(2, { trials: { n: 2, passRule: { kind: 'aggregate', thresholdsRef: 'eval-set' } } });
  const trials = [gauntletTrial(scenario, 0, [1, 0.5]), gauntletTrial(scenario, 1, [0.5, 0.5])];
  const stats = meanDecayAcrossTrials(scenario, trials);
  assert.ok(stats);
  assert.equal(stats!.itemCount, 2);
  assert.ok(Math.abs(stats!.auc - 0.625) < 1e-9); // ((1+0.5)/2 + (0.5+0.5)/2) / 2
});

test('error verdicts are never green: otherwise-passing scenario gets status error', () => {
  const scenario = makeScenario({ trials: { n: 1, passRule: { kind: 'at_least', k: 1 } } });
  const trials = [
    qualityTrial(scenario, 0, [
      v({ graderId: 'q1' }),
      v({ graderId: 'judge', status: 'error', score: 0 }),
    ]),
  ];
  const verdict = buildScenarioVerdict(scenario, trials, config);
  assert.equal(verdict.status, 'error');

  // Advisory errors (uncalibrated judge cache miss) do not flip the status.
  const advisoryTrials = [
    qualityTrial(scenario, 0, [
      v({ graderId: 'q1' }),
      v({ graderId: 'judge', status: 'error', score: 0, advisory: true }),
    ]),
  ];
  assert.equal(buildScenarioVerdict(scenario, advisoryTrials, config).status, 'passed');
});

test('quarantined scenarios report status quarantined but keep the invariant flag', () => {
  const scenario = makeScenario({ trials: { n: 1, passRule: { kind: 'at_least', k: 1 } } });
  const trials = [
    qualityTrial(scenario, 0, [v({ graderId: 'inv', class: 'invariant', status: 'fail', score: 0 })]),
  ];
  const verdict = buildScenarioVerdict(scenario, trials, config, { quarantined: true });
  assert.equal(verdict.status, 'quarantined');
  assert.equal(verdict.invariantViolation, true);
});

// ---------------------------------------------------------------------------
// suiteGreen + buildSuiteResult
// ---------------------------------------------------------------------------

function scnVerdict(partial: Partial<ScenarioVerdict> & { scenarioId: string }): ScenarioVerdict {
  return {
    status: 'passed',
    invariantViolation: false,
    trials: [],
    meanDecay: null,
    passDetail: '',
    totalCostUsd: 0,
    ...partial,
  };
}

test('suiteGreen: green iff no invariant violations, all passed, zero error verdicts', () => {
  const allGood = suiteGreen([scnVerdict({ scenarioId: 's1' }), scnVerdict({ scenarioId: 's2' })], config);
  assert.equal(allGood.green, true);

  const failed = suiteGreen([scnVerdict({ scenarioId: 's1', status: 'failed' })], config);
  assert.equal(failed.green, false);

  const invariant = suiteGreen(
    [scnVerdict({ scenarioId: 's1', status: 'failed', invariantViolation: true })],
    config
  );
  assert.equal(invariant.green, false);
  assert.ok(invariant.detail.some((d) => d.includes('invariant')));
});

test('suiteGreen blocks on non-advisory error verdicts buried inside trials', () => {
  const scenario = makeScenario();
  const trial = qualityTrial(scenario, 0, [
    v({ graderId: 'q1' }),
    v({ graderId: 'boom', status: 'error', score: 0 }),
  ]);
  // Even if aggregation mislabeled the scenario passed, suiteGreen still blocks.
  const out = suiteGreen([scnVerdict({ scenarioId: 's1', status: 'passed', trials: [trial] })], config);
  assert.equal(out.green, false);
  assert.ok(out.detail.some((d) => d.includes('error verdict')));

  const advisoryTrial = qualityTrial(scenario, 1, [
    v({ graderId: 'q1' }),
    v({ graderId: 'judge', status: 'error', score: 0, advisory: true }),
  ]);
  const ok = suiteGreen([scnVerdict({ scenarioId: 's1', status: 'passed', trials: [advisoryTrial] })], config);
  assert.equal(ok.green, true);
  assert.ok(ok.detail.some((d) => d.includes('advisory'))); // excluded-but-noted
});

test('suiteGreen excludes quarantined scenarios from gating but notes them', () => {
  const out = suiteGreen(
    [
      scnVerdict({ scenarioId: 's1' }),
      scnVerdict({ scenarioId: 's-flaky', status: 'quarantined', invariantViolation: true }),
    ],
    config
  );
  assert.equal(out.green, true);
  assert.ok(out.detail.some((d) => d.includes('quarantined')));
  assert.ok(out.detail.some((d) => d.includes('WARNING') && d.includes('s-flaky')));
});

test('suiteGreen enforces the cost regression guard against the baseline', () => {
  const scenarios = [scnVerdict({ scenarioId: 's1', totalCostUsd: 12 })];
  const within = suiteGreen(scenarios, config, { baselineCostUsd: 10 }); // cap 12.5
  assert.equal(within.green, true);

  const over = suiteGreen([scnVerdict({ scenarioId: 's1', totalCostUsd: 13 })], config, {
    baselineCostUsd: 10,
  });
  assert.equal(over.green, false);
  assert.ok(over.detail.some((d) => d.includes('cost regression')));
});

test('buildSuiteResult assembles a schema-valid SuiteResult', () => {
  const result = buildSuiteResult({
    config,
    subject: { kind: 'scripted', label: 'golden' },
    startedAt: '2026-08-02T00:00:00.000Z',
    finishedAt: '2026-08-02T00:05:00.000Z',
    scenarios: [scnVerdict({ scenarioId: 's1', totalCostUsd: 2.5 })],
  });
  SuiteResultSchema.parse(result);
  assert.equal(result.green, true);
  assert.equal(result.totalCostUsd, 2.5);
  assert.equal(result.evalSet.name, 'convoy-core');
});
