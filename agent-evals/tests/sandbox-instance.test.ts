/**
 * Full-loop tests: createSandboxService + runUntil driving a minimal inline
 * fake RuntimeClient (a tiny state machine — deliberately NOT the executors
 * package): email out → park on timer → cooperative reply → approval gate →
 * scripted approve → land. Plus the deadlock and sim-guard paths.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { DrainReport, OpenGate, ResolveGateInput, RuntimeClient } from '../src/runtime/ports.ts';
import { ScenarioSchema, type Scenario } from '../src/schema/scenario.ts';
import { createSandboxService } from '../src/sandbox/instance.ts';
import type { RuntimeFactory } from '../src/sandbox/api.ts';

const HERE = dirname(fileURLToPath(import.meta.url));
const PACK_MINI = join(HERE, 'fixtures', 'pack-mini');
const T0 = new Date('2026-08-01T00:00:00Z');
const DAY = 86_400_000;

function makeScenario(over: Partial<Scenario> = {}): Scenario {
  return ScenarioSchema.parse({
    id: 'sc-mini',
    title: 'mini renewal',
    missionType: 'renewal',
    kind: 'single',
    fixture: { pack: 'pack-mini', packHash: 'test-unhashed' },
    t0: '2026-08-01',
    seed: 42,
    trigger: { kind: 'api', missionSpec: { missionType: 'renewal', goal: 'Renew POL-1' } },
    counterparties: [
      {
        actorId: 'carrier',
        channel: 'email',
        owns: ['carrier@acme.sim'],
        profile: 'cooperative',
        params: {
          replyTemplate: { subject: 'RE: Loss runs', body: 'Attached as requested.' },
          attachments: ['attachments/loss-runs.txt'],
        },
      },
    ],
    approvals: {
      mode: 'scripted',
      steps: [
        {
          id: 's1',
          expect: { kind: 'action-approval', payload: { all: [{ path: 'action', op: 'eq', value: 'bind' }] } },
          resolve: { kind: 'approve' },
        },
      ],
    },
    budgets: { usd: 5, simTime: '30d', wallClock: '60s' },
    answerKeyRef: { path: 'keys/mini.json', hash: 'test-unhashed' },
    graders: [],
    provenance: { kind: 'authored' },
    ...over,
  });
}

/**
 * Fake renewal runtime: send loss-runs request → park on a 3d follow-up timer
 * → once the reply is in, raise a bind approval gate → once approved, update
 * the policy and land.
 */
function makeFakeRenewalRuntime() {
  const state = {
    drains: [] as string[],
    resolveCalls: [] as ResolveGateInput[],
    phase: 'init' as 'init' | 'waiting' | 'gated' | 'approved',
    timerAt: null as Date | null,
  };
  const gate: OpenGate = {
    gateId: 'g1',
    kind: 'action-approval',
    deadlineAt: null,
    payload: { action: 'bind', policyId: 'POL-1' },
    stepTag: 'bind-policy',
  };
  const factory: RuntimeFactory = (env) => {
    const client: RuntimeClient = {
      async startMission() {
        return 'm-fake';
      },
      async drain(missionId, deps): Promise<DrainReport> {
        const now = deps.clock.now();
        state.drains.push(now.toISOString());
        if (state.phase === 'init') {
          await env.gateway.invoke(
            'email.send',
            { to: 'carrier@acme.sim', subject: 'Loss runs request', body: 'Please send loss runs for POL-1.' },
            { missionId }
          );
          state.timerAt = new Date(now.getTime() + 3 * DAY);
          state.phase = 'waiting';
          return { terminal: null, openGates: [], nextTimerAt: state.timerAt, stepsExecuted: 1 };
        }
        if (state.phase === 'waiting') {
          if (now.getTime() < state.timerAt!.getTime()) {
            return { terminal: null, openGates: [], nextTimerAt: state.timerAt, stepsExecuted: 0 };
          }
          const reply = env.world
            .listMessages({ direction: 'inbound' })
            .find((m) => m.subject.startsWith('RE:'));
          assert.ok(reply, 'cooperative reply must have been delivered before the 3d timer fired');
          state.phase = 'gated';
          return { terminal: null, openGates: [gate], nextTimerAt: null, stepsExecuted: 1 };
        }
        if (state.phase === 'gated') {
          return { terminal: null, openGates: [gate], nextTimerAt: null, stepsExecuted: 0 };
        }
        // approved
        await env.gateway.invoke(
          'ams.update_policy',
          { policyId: 'POL-1', fields: { renewal_status: 'bound' } },
          { missionId }
        );
        return { terminal: 'landed', openGates: [], nextTimerAt: null, stepsExecuted: 1 };
      },
      async resolveGate(input) {
        state.resolveCalls.push(input);
        if (input.gateId === 'g1' && input.resolution.kind === 'approve') state.phase = 'approved';
      },
    };
    return client;
  };
  return { factory, state };
}

/** Fake runtime that emails a counterparty then parks on awaited inbound with NO timer. */
function makeParkedRuntime() {
  let sent = false;
  const factory: RuntimeFactory = (env) => ({
    async startMission() {
      return 'm-parked';
    },
    async drain(missionId): Promise<DrainReport> {
      if (!sent) {
        sent = true;
        await env.gateway.invoke(
          'email.send',
          { to: 'carrier@acme.sim', subject: 'Anyone there?', body: 'Please reply.' },
          { missionId }
        );
      }
      const reply = env.world.listMessages({ direction: 'inbound' }).find((m) => m.subject.startsWith('RE:'));
      if (reply) return { terminal: 'landed', openGates: [], nextTimerAt: null, stepsExecuted: 1 };
      return { terminal: null, openGates: [], nextTimerAt: null, stepsExecuted: 1 };
    },
    async resolveGate() {},
  });
  return factory;
}

// ---------------------------------------------------------------------------
// (f) full runUntil
// ---------------------------------------------------------------------------

test('runUntil: email → timer → cooperative reply → gate approved by script → landed', async () => {
  const { factory, state } = makeFakeRenewalRuntime();
  const service = createSandboxService();
  const instance = await service.create(makeScenario(), factory, { packDir: PACK_MINI });

  const report = await instance.runUntil({ kind: 'terminal' });

  assert.equal(report.terminal, 'landed');
  assert.equal(report.deadlock, false);
  assert.equal(report.guardTripped, null);
  assert.ok(report.simNow.getTime() >= T0.getTime() + 3 * DAY, 'sim time advanced through the timer');

  // DES advance order: t0 (send) → reply delivery (1-2d) → timer fire (3d) → same-instant approved drain.
  const drainTimes = state.drains.map((iso) => new Date(iso).getTime());
  for (let i = 1; i < drainTimes.length; i++) {
    assert.ok(drainTimes[i]! >= drainTimes[i - 1]!, 'sim clock never goes backwards across drains');
  }
  const replyDrain = drainTimes[1]!;
  assert.ok(replyDrain > T0.getTime() + DAY && replyDrain < T0.getTime() + 2 * DAY, 'woken for the reply delivery');
  assert.equal(state.drains[2], new Date(T0.getTime() + 3 * DAY).toISOString(), 'then woken for the timer');

  // Gate resolution: attributed to the script step, mirrored into the log.
  assert.equal(state.resolveCalls.length, 1);
  assert.equal(state.resolveCalls[0]!.gateId, 'g1');
  assert.equal(state.resolveCalls[0]!.resolvedBy, 'harness:s1');
  const gateReport = instance.gateReport();
  assert.deepEqual(gateReport.resolutions, [{ gateId: 'g1', stepId: 's1', resolution: 'approve' }]);
  assert.deepEqual(gateReport.neverRaised, []);
  assert.deepEqual(gateReport.unexpected, []);

  // End state: world reflects the approved action; teardown bundle carries it.
  assert.deepEqual(instance.world.query('records.policy.POL-1.renewal_status'), ['bound']);
  const { events, world } = instance.destroy();
  assert.ok(events.some((e) => e.type === 'human_intervention' && e.kind === 'gate_resolution'));
  assert.ok(events.some((e) => e.type === 'tool_intent' && e.tool === 'email.send'));
  assert.ok(events.some((e) => e.type === 'tool_result' && e.tool === 'ams.update_policy'));
  const reply = world.messages.find((m) => m.direction === 'inbound' && m.subject === 'RE: Loss runs');
  assert.ok(reply, 'counterparty reply is in the exported bundle');
  assert.deepEqual(reply.attachments.map((a) => a.name), ['loss-runs.txt']);
  assert.ok(world.hash.length === 64);
});

test('runUntil: stop {gate-open} halts with the gate still open', async () => {
  const { factory } = makeFakeRenewalRuntime();
  const instance = await createSandboxService().create(makeScenario(), factory, { packDir: PACK_MINI });
  const report = await instance.runUntil({ kind: 'gate-open' });
  assert.equal(report.terminal, null);
  assert.equal(report.openGates.length, 1);
  assert.equal(report.openGates[0]!.gateId, 'g1');
  assert.deepEqual(instance.gateReport().resolutions, [], 'script has not resolved anything yet');
});

// ---------------------------------------------------------------------------
// (g) deadlock
// ---------------------------------------------------------------------------

test('runUntil: silent counterparty + parked runtime with no timer → deadlock with diagnosis', async () => {
  const scenario = makeScenario({
    counterparties: [
      { actorId: 'carrier', channel: 'email', owns: ['carrier@acme.sim'], profile: 'silent', default: 'silent' },
    ] as Scenario['counterparties'],
  });
  const instance = await createSandboxService().create(scenario, makeParkedRuntime(), { packDir: PACK_MINI });
  const report = await instance.runUntil({ kind: 'terminal' });

  assert.equal(report.terminal, null);
  assert.equal(report.deadlock, true);
  assert.ok(report.deadlockDiagnosis);
  assert.match(report.deadlockDiagnosis, /no pending timers/);
  assert.match(report.deadlockDiagnosis, /no queued counterparty deliveries/);
  assert.match(report.deadlockDiagnosis, /no open gates/);
  assert.equal(report.simNow.toISOString(), T0.toISOString(), 'nothing ever advanced the clock');
});

// ---------------------------------------------------------------------------
// guards
// ---------------------------------------------------------------------------

test('runUntil: next event beyond t0+budgets.simTime trips the sim guard', async () => {
  const scenario = makeScenario({ budgets: { usd: 5, simTime: '2d', wallClock: '60s', onExhaustion: 'fail' } });
  // Cooperative reply would land within 1-2d of the send, but the runtime only
  // wakes for its 3d timer — beyond the 2d sim budget once the reply is consumed.
  const { factory } = makeFakeRenewalRuntime();
  const instance = await createSandboxService().create(scenario, factory, { packDir: PACK_MINI });
  const report = await instance.runUntil({ kind: 'terminal' });
  assert.equal(report.guardTripped, 'sim');
  assert.equal(report.terminal, null);
});

test('runUntil: stop {sim-time} caps the advance at the requested instant', async () => {
  const { factory } = makeFakeRenewalRuntime();
  const instance = await createSandboxService().create(makeScenario(), factory, { packDir: PACK_MINI });
  const stopAt = new Date(T0.getTime() + DAY / 2);
  const report = await instance.runUntil({ kind: 'sim-time', at: stopAt });
  assert.equal(report.terminal, null);
  assert.equal(report.simNow.toISOString(), stopAt.toISOString());
});
