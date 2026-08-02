/**
 * runner.test.ts — integration tests for the suite runner, replay tier, and
 * reports, driven over the generated corpus (scenarios/sets/renewal-prep-v1.json).
 *
 * Tests (a)-(d) exercise the full pipeline and therefore need the sibling
 * components (src/sandbox, src/executors, src/graders, src/scoring) plus the
 * corpus to exist — they are written ahead of those landing and will fail with
 * module-not-found until then (imports are dynamic so each failure is scoped
 * to its test). Test (e) covers the report renderers, which are self-contained.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

import type { ConvoyEvent } from '../src/runtime/events.ts';
import type { WorldBundle } from '../src/sandbox/api.ts';
import { ScenarioSchema, type Scenario } from '../src/schema/scenario.ts';
import type { SuiteResult, TrialResult, Verdict } from '../src/schema/verdict.ts';
import { renderRehearsalReport } from '../src/reports/rehearsal-report.ts';
import { renderSuiteReport } from '../src/reports/suite-report.ts';

const SET_PATH = fileURLToPath(new URL('../scenarios/sets/renewal-prep-v1.json', import.meta.url));
const GOLDEN_SCENARIO = 'renewal-golden-3';

function freshDir(name: string): string {
  return mkdtempSync(join(tmpdir(), `convoy-evals-${name}-`));
}

/** Shared across sequential tests: (d) replays (a)'s recorded artifacts. */
let goldenRun: { outDir: string; result: SuiteResult } | undefined;

// ---------------------------------------------------------------------------
// (a) golden executor → scenario passes, artifacts written, suite green
// ---------------------------------------------------------------------------

test('(a) runSuite scripted:golden passes renewal-golden-3, writes artifacts, suite is green', async () => {
  const { runSuite } = await import('../src/runner/suite-runner.ts');
  const outDir = freshDir('golden');
  const result = await runSuite({
    evalSetPath: SET_PATH,
    subject: { kind: 'scripted', executor: 'golden' },
    scenarioFilter: [GOLDEN_SCENARIO],
    judgeMode: 'cache-only',
    outDir,
  });
  goldenRun = { outDir, result };

  assert.equal(result.scenarios.length, 1, 'filter should leave exactly one scenario');
  const sv = result.scenarios[0];
  assert.ok(sv, 'scenario verdict present');
  assert.equal(sv.scenarioId, GOLDEN_SCENARIO);
  assert.equal(sv.status, 'passed', `expected passed, got ${sv.status}: ${sv.passDetail}`);
  assert.equal(sv.invariantViolation, false, 'golden run must not violate invariants');
  assert.ok(sv.trials.length >= 1, 'at least one trial ran');

  assert.ok(existsSync(join(outDir, `events-${GOLDEN_SCENARIO}-t0.jsonl`)), 'events JSONL written');
  assert.ok(existsSync(join(outDir, `world-${GOLDEN_SCENARIO}-t0.json`)), 'world bundle written');
  assert.ok(existsSync(join(outDir, `verdicts-${GOLDEN_SCENARIO}-t0.json`)), 'verdicts JSON written');
  assert.ok(existsSync(join(outDir, 'suite-result.json')), 'suite-result.json written');

  assert.equal(result.green, true, `suite should be green given only the golden scenario: ${result.greenDetail.join('; ')}`);
});

// ---------------------------------------------------------------------------
// (b) violator-no-gate → invariant violation, suite not green
// ---------------------------------------------------------------------------

test('(b) runSuite scripted:violator-no-gate flags invariantViolation and the suite is not green', async () => {
  const { runSuite } = await import('../src/runner/suite-runner.ts');
  const result = await runSuite({
    evalSetPath: SET_PATH,
    subject: { kind: 'scripted', executor: 'violator-no-gate' },
    scenarioFilter: [GOLDEN_SCENARIO],
    judgeMode: 'cache-only',
    outDir: freshDir('violator-no-gate'),
  });
  const sv = result.scenarios[0];
  assert.ok(sv, 'scenario verdict present');
  assert.equal(sv.invariantViolation, true, 'skipping the gate must violate an invariant');
  assert.notEqual(sv.status, 'passed', 'violator must not pass');
  assert.equal(result.green, false, 'suite must not be green with an invariant violation');
});

// ---------------------------------------------------------------------------
// (c) violator-skips-items on the gauntlet → decay stats drop, scenario fails
// ---------------------------------------------------------------------------

test('(c) runSuite scripted:violator-skips-items on the gauntlet fails the scenario (skipped items pull Q down)', async (t) => {
  const { loadEvalSet, loadScenario } = await import('../src/runner/store.ts');
  const loaded = loadEvalSet(SET_PATH);
  let gauntletId: string | undefined;
  for (const [id, path] of loaded.scenarioPaths) {
    try {
      if (loadScenario(path).kind === 'gauntlet') {
        gauntletId = id;
        break;
      }
    } catch {
      // unparseable scenario — lint's problem, not this test's
    }
  }
  if (!gauntletId) {
    t.skip('no gauntlet scenario present in the corpus yet');
    return;
  }

  const { runSuite } = await import('../src/runner/suite-runner.ts');
  const result = await runSuite({
    evalSetPath: SET_PATH,
    subject: { kind: 'scripted', executor: 'violator-skips-items' },
    scenarioFilter: [gauntletId],
    trialsOverride: 1,
    judgeMode: 'cache-only',
    outDir: freshDir('skips-items'),
  });
  const sv = result.scenarios[0];
  assert.ok(sv, 'scenario verdict present');
  assert.equal(sv.status, 'failed', `skipping items must fail the gauntlet: ${sv.passDetail}`);
  if (sv.meanDecay) {
    assert.ok(
      sv.meanDecay.minQ === 0 || sv.meanDecay.auc < 1,
      `missing items should drag decay stats down (minQ=${sv.meanDecay.minQ}, auc=${sv.meanDecay.auc})`
    );
  }
});

// ---------------------------------------------------------------------------
// (d) replay determinism over (a)'s artifacts
// ---------------------------------------------------------------------------

/** Flatten every verdict to a sortable [scenario, trial, grader, item, status] row. */
function verdictStatuses(result: SuiteResult): string[] {
  const rows: string[] = [];
  for (const s of result.scenarios) {
    for (const trial of s.trials) {
      for (const v of trial.verdicts) {
        rows.push(`${s.scenarioId}|t${trial.trialIdx}|${v.graderId}|${v.scope.itemId ?? ''}|${v.status}`);
      }
      for (const item of trial.items) {
        for (const v of item.verdicts) {
          rows.push(`${s.scenarioId}|t${trial.trialIdx}|${v.graderId}|${item.itemId}|${v.status}`);
        }
      }
    }
  }
  return rows.sort();
}

test('(d) replaySuite over the golden outDir reproduces identical verdict statuses (determinism)', async () => {
  assert.ok(goldenRun, 'depends on test (a) having run');
  const { replaySuite } = await import('../src/runner/replay.ts');
  const replayed = await replaySuite({ evalSetPath: SET_PATH, recordsDir: goldenRun.outDir });

  assert.equal(replayed.subject.label, 'replay');
  assert.deepEqual(
    replayed.scenarios.map((s) => [s.scenarioId, s.status, s.invariantViolation]),
    goldenRun.result.scenarios.map((s) => [s.scenarioId, s.status, s.invariantViolation]),
    'scenario statuses must match the original run'
  );
  assert.deepEqual(verdictStatuses(replayed), verdictStatuses(goldenRun.result), 'per-verdict statuses must be identical');
});

// ---------------------------------------------------------------------------
// (e) report renderers — self-contained (no sibling components needed)
// ---------------------------------------------------------------------------

function syntheticSuiteResult(): SuiteResult {
  const failVerdict: Verdict = {
    graderId: 'end_state.renewal_status',
    graderVersion: 'abc123',
    class: 'quality',
    scope: { runId: 'r1', itemId: 'packet/POL-2' },
    status: 'fail',
    score: 0.6,
    evidence: [{ kind: 'world', query: 'records.policy.POL-2.renewal_status', result: ['pending'] }],
  };
  return {
    evalSet: { name: 'synthetic-set', version: '0' },
    subject: { kind: 'scripted', label: 'scripted:golden' },
    startedAt: '2026-08-02T00:00:00.000Z',
    finishedAt: '2026-08-02T00:01:00.000Z',
    scenarios: [
      {
        scenarioId: 'synthetic-gauntlet',
        status: 'passed',
        invariantViolation: false,
        trials: [
          {
            trialIdx: 0,
            runId: 'r1',
            status: 'completed',
            verdicts: [],
            items: [
              { itemId: 'packet/POL-1', ordinal: 1, q: 1, status: 'pass', verdicts: [] },
              { itemId: 'packet/POL-2', ordinal: 2, q: 0.6, status: 'fail', verdicts: [failVerdict] },
              { itemId: 'packet/POL-3', ordinal: 3, q: 0.9, status: 'pass', verdicts: [] },
            ],
            decay: { slope: -0.05, auc: 0.83, firstDecileMean: 1, lastDecileMean: 0.9, itemCount: 3, minQ: 0.6 },
            costUsd: 0.42,
            simDays: 4.5,
            wallMs: 1234,
          },
        ],
        meanDecay: { slope: -0.05, auc: 0.83, firstDecileMean: 1, lastDecileMean: 0.9, itemCount: 3, minQ: 0.6 },
        passDetail: '1/1 trials passed',
        totalCostUsd: 0.42,
      },
    ],
    green: true,
    greenDetail: ['all scenarios passed'],
    totalCostUsd: 0.42,
  };
}

function syntheticScenario(): Scenario {
  return ScenarioSchema.parse({
    id: 'synthetic-rehearsal',
    title: 'Synthetic rehearsal scenario',
    missionType: 'renewal-prep',
    kind: 'single',
    fixture: { pack: 'none', packHash: 'deadbeef' },
    t0: '2026-08-01',
    seed: 7,
    trigger: { kind: 'api', missionSpec: { missionType: 'renewal-prep', goal: 'Prepare the renewal packet for POL-1' } },
    counterparties: [{ actorId: 'broker-jane', channel: 'email', owns: ['jane@broker.example'], profile: 'cooperative' }],
    approvals: { mode: 'auto_approve', maxGates: 5 },
    budgets: { usd: 2, simTime: '10d', wallClock: '5m' },
    answerKeyRef: { path: 'keys/synthetic.key.json', hash: 'deadbeef' },
    graders: [],
    provenance: { kind: 'authored' },
  });
}

function syntheticEvents(): ConvoyEvent[] {
  const base = { missionId: 'm1', wallTs: '2026-08-02T00:00:00.000Z' };
  return [
    {
      ...base,
      eventId: 'e1',
      seq: 0,
      ts: '2026-08-01T09:00:00.000Z',
      type: 'mission_started',
      missionType: 'renewal-prep',
      environmentId: 'env-1',
      goal: 'Prepare the renewal packet for POL-1',
      spec: { modelId: 'scripted', promptHashes: {} },
    },
    {
      ...base,
      eventId: 'e2',
      seq: 1,
      ts: '2026-08-01T10:00:00.000Z',
      type: 'gate_raised',
      gateId: 'g1',
      kind: 'action-approval',
      payload: { action: 'send_renewal_packet' },
      deadlineAt: null,
      stepTag: 'send-packet',
    },
    {
      ...base,
      eventId: 'e3',
      seq: 2,
      ts: '2026-08-01T12:30:00.000Z',
      type: 'gate_resolved',
      gateId: 'g1',
      resolution: 'approve',
      resolvedBy: 'ops@customer.example',
      reason: 'packet matches the checklist',
    },
    {
      ...base,
      eventId: 'e4',
      seq: 3,
      ts: '2026-08-01T12:35:00.000Z',
      type: 'artifact_created',
      artifactId: 'a1',
      hash: 'f00dfeed00112233',
      tag: 'renewal-packet',
      mime: 'application/pdf',
    },
    {
      ...base,
      eventId: 'e5',
      seq: 4,
      ts: '2026-08-02T09:00:00.000Z',
      type: 'terminal_outcome',
      status: 'landed',
      judgedBy: 'agent',
      summary: 'packet sent and confirmed',
    },
  ];
}

function syntheticWorld(): WorldBundle {
  return {
    hash: 'worldhash1',
    messages: [
      {
        id: 'msg1',
        threadId: 'th1',
        from: 'jane@broker.example',
        to: ['agent@convoy.example'],
        subject: 'Loss runs attached',
        body: 'Here you go.',
        attachments: [{ name: 'loss-runs-2025.pdf', fileId: 'f1' }],
        ts: '2026-08-01T11:00:00.000Z',
        direction: 'inbound',
      },
    ],
    records: [],
    files: [],
  };
}

test('(e) suite report contains the scenario id and an <svg; rehearsal report renders a gate row', () => {
  const suiteHtml = renderSuiteReport(syntheticSuiteResult(), { itemFloor: 0.7 });
  assert.ok(suiteHtml.includes('synthetic-gauntlet'), 'suite report names the scenario');
  assert.ok(suiteHtml.includes('<svg'), 'suite report embeds inline SVG');
  assert.ok(suiteHtml.includes('item floor 0.7'), 'Q(n) chart draws the item-floor threshold');
  assert.ok(!suiteHtml.includes('src='), 'self-contained: no external asset references');

  const trial: TrialResult = {
    trialIdx: 0,
    runId: 'm1',
    status: 'completed',
    verdicts: [
      {
        graderId: 'trajectory.gate_before_send',
        graderVersion: 'v1',
        class: 'invariant',
        scope: { runId: 'm1' },
        status: 'pass',
        score: 1,
        evidence: [{ kind: 'event', eventId: 'e2', note: 'gate raised before the send' }],
      },
    ],
    items: [],
    decay: null,
    costUsd: 0.12,
    simDays: 1.0,
    wallMs: 500,
  };
  const rehearsalHtml = renderRehearsalReport({
    scenario: syntheticScenario(),
    trial,
    events: syntheticEvents(),
    world: syntheticWorld(),
  });
  assert.ok(rehearsalHtml.includes('Gate raised'), 'timeline shows the gate row');
  assert.ok(rehearsalHtml.includes('approve'), 'gate row shows the resolution');
  assert.ok(rehearsalHtml.includes('ops@customer.example'), 'gate row names the approver');
  assert.ok(rehearsalHtml.includes('broker-jane'), 'inbound message is labeled with the counterparty actor');
  assert.ok(rehearsalHtml.includes('Sign-off'), 'sign-off footer block present');
});
