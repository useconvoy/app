/**
 * Engine tests: counterparty profiles (cooperative / slow / portal) and the
 * gate-script engine (scripted matching, scheduling, unexpected, neverRaised).
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { OpenGate, GateResolutionWire } from '../src/runtime/ports.ts';
import type { CounterpartyScript, ApprovalScript } from '../src/schema/scenario.ts';
import { createSimClock } from '../src/sandbox/clock.ts';
import { createWorldStore } from '../src/sandbox/world.ts';
import { createCounterpartyEngine } from '../src/sandbox/counterparty.ts';
import { createGateScriptEngine } from '../src/sandbox/gates.ts';
import type { WorldEvent } from '../src/sandbox/api.ts';

const HERE = dirname(fileURLToPath(import.meta.url));
const PACK_MINI = join(HERE, 'fixtures', 'pack-mini');
const T0 = new Date('2026-08-01T00:00:00Z');
const DAY = 86_400_000;

function outboundToCarrier(world: ReturnType<typeof createWorldStore>, subject = 'Loss runs please') {
  return world.sendMessage({
    threadId: 'thread_loss-runs',
    from: 'agent@convoy.sim',
    to: ['carrier@acme.sim'],
    subject,
    body: 'Requesting loss runs for POL-1.',
    attachments: [],
    direction: 'outbound',
  });
}

function carrierScript(profile: CounterpartyScript['profile']): CounterpartyScript {
  return {
    actorId: 'carrier',
    channel: 'email',
    owns: ['carrier@acme.sim'],
    profile,
    params: {
      replyTemplate: { subject: 'RE: Loss runs', body: 'Attached as requested.' },
      attachments: ['attachments/loss-runs.txt'],
    },
    default: 'silent',
  } as CounterpartyScript;
}

// ---------------------------------------------------------------------------
// (d) counterparty
// ---------------------------------------------------------------------------

test('counterparty: cooperative replies 1-2 sim-days after the outbound, with the correct doc', () => {
  const clock = createSimClock(T0);
  const world = createWorldStore(clock, 7);
  world.seedFromPack(PACK_MINI, T0, 7);
  const engine = createCounterpartyEngine({ scripts: [carrierScript('cooperative')], world, clock, seed: 7, packDir: PACK_MINI });

  assert.equal(engine.peek(), null, 'quiet until the agent writes');
  outboundToCarrier(world);

  const at = engine.peek();
  assert.ok(at, 'reply queued');
  const delay = at.getTime() - T0.getTime();
  assert.ok(delay >= 1 * DAY && delay <= 2 * DAY, `delay ${delay / DAY}d must be within [1d, 2d]`);

  clock.advanceTo(at);
  engine.deliverDue(clock.now());
  assert.equal(engine.peek(), null);

  // The seeded pack contains one inbound kickoff message; the reply threads onto our ask.
  const inbound = world.listMessages({ direction: 'inbound', threadId: 'thread_loss-runs' });
  assert.equal(inbound.length, 1);
  const reply = inbound[0]!;
  assert.equal(reply.from, 'carrier@acme.sim');
  assert.equal(reply.subject, 'RE: Loss runs');
  assert.equal(reply.threadId, 'thread_loss-runs', 'reply stays on the triggering thread');
  assert.deepEqual(reply.attachments.map((a) => a.name), ['loss-runs.txt']);
  const file = world.getFile(reply.attachments[0]!.fileId)!;
  assert.match(file.content, /LOSS RUN REPORT/);
});

test('counterparty: cooperative reply delivery is deterministic under the seed', () => {
  const run = () => {
    const clock = createSimClock(T0);
    const world = createWorldStore(clock, 7);
    world.seedFromPack(PACK_MINI, T0, 7);
    const engine = createCounterpartyEngine({ scripts: [carrierScript('cooperative')], world, clock, seed: 7, packDir: PACK_MINI });
    outboundToCarrier(world);
    return engine.peek()!.toISOString();
  };
  assert.equal(run(), run());
});

test('counterparty: slow replies only to the chase (2nd matching message)', () => {
  const clock = createSimClock(T0);
  const world = createWorldStore(clock, 11);
  world.seedFromPack(PACK_MINI, T0, 11);
  const engine = createCounterpartyEngine({ scripts: [carrierScript('slow')], world, clock, seed: 11, packDir: PACK_MINI });

  outboundToCarrier(world, 'Loss runs please');
  assert.equal(engine.peek(), null, 'first ask is ignored');

  clock.advanceTo(new Date(T0.getTime() + 3 * DAY));
  outboundToCarrier(world, 'Following up: loss runs');
  const at = engine.peek();
  assert.ok(at, 'the chase gets a reply');
  clock.advanceTo(at);
  engine.deliverDue(clock.now());

  const inbound = world.listMessages({ direction: 'inbound', threadId: 'thread_loss-runs' });
  assert.equal(inbound.length, 1, 'exactly one reply, to the chase');
});

test('counterparty: messages to unowned addresses are invisible to the actor', () => {
  const clock = createSimClock(T0);
  const world = createWorldStore(clock, 3);
  const engine = createCounterpartyEngine({ scripts: [carrierScript('cooperative')], world, clock, seed: 3, packDir: PACK_MINI });
  world.sendMessage({
    threadId: 't', from: 'agent@convoy.sim', to: ['someoneelse@other.sim'], subject: 's', body: 'b',
    attachments: [], direction: 'outbound',
  });
  assert.equal(engine.peek(), null);
});

test('counterparty: portal actor fulfills pending requests (record + portal_transition)', () => {
  const clock = createSimClock(T0);
  const world = createWorldStore(clock, 5);
  world.seedFromPack(PACK_MINI, T0, 5);
  const events: WorldEvent[] = [];
  world.onEvent((e) => events.push(e));
  const portalScript = {
    actorId: 'sentinel-portal',
    channel: 'portal',
    owns: ['sentinel'],
    profile: 'cooperative',
    params: { document: 'attachments/loss-runs.txt' },
    default: 'silent',
  } as CounterpartyScript;
  const engine = createCounterpartyEngine({ scripts: [portalScript], world, clock, seed: 5, packDir: PACK_MINI });

  world.upsertRecord('portal_request', 'req_1', { status: 'pending', carrier: 'sentinel', policyId: 'POL-1' });
  const at = engine.peek();
  assert.ok(at, 'fulfillment scheduled');
  clock.advanceTo(at);
  engine.deliverDue(clock.now());

  const record = world.getRecord('portal_request', 'req_1')!;
  assert.equal(record.fields['status'], 'fulfilled');
  const fileId = record.fields['documentFileId'];
  assert.equal(typeof fileId, 'string');
  assert.match(world.getFile(fileId as string)!.content, /LOSS RUN REPORT/);
  assert.ok(events.some((e) => e.kind === 'portal_transition' && e.requestId === 'req_1' && e.to === 'fulfilled'));
});

test('counterparty: unsolicited sends arrive at t0+atSim', () => {
  const clock = createSimClock(T0);
  const world = createWorldStore(clock, 9);
  const script = {
    ...carrierScript('silent'),
    unsolicited: [{ atSim: '5d', message: { subject: 'Notice of non-renewal', body: 'FYI.' } }],
  } as CounterpartyScript;
  const engine = createCounterpartyEngine({ scripts: [script], world, clock, seed: 9, packDir: PACK_MINI });

  const at = engine.peek();
  assert.ok(at);
  assert.equal(at.toISOString(), new Date(T0.getTime() + 5 * DAY).toISOString());
  clock.advanceTo(at);
  engine.deliverDue(clock.now());
  const inbound = world.listMessages({ direction: 'inbound' });
  assert.equal(inbound.length, 1);
  assert.equal(inbound[0]!.subject, 'Notice of non-renewal');
});

// ---------------------------------------------------------------------------
// (e) gate script engine
// ---------------------------------------------------------------------------

type Resolved = { gateId: string; wire: GateResolutionWire; resolvedBy: string; reason?: string };

function recorder() {
  const calls: Resolved[] = [];
  const fn = async (gateId: string, wire: GateResolutionWire, resolvedBy: string, reason?: string) => {
    calls.push({ gateId, wire, resolvedBy, reason });
  };
  return { calls, fn };
}

function gate(gateId: string, kind: OpenGate['kind'], payload: unknown): OpenGate {
  return { gateId, kind, deadlineAt: null, payload };
}

const SCRIPT: ApprovalScript = {
  mode: 'scripted',
  steps: [
    {
      id: 's1',
      expect: { kind: 'action-approval', payload: { all: [{ path: 'action', op: 'eq', value: 'bind' }] } },
      resolve: { kind: 'approve' },
      optional: false,
      ordered: true,
      maxFires: 1,
    },
    {
      id: 's2',
      expect: { kind: 'input-request' },
      resolve: { kind: 'provide_input', payload: { answer: 42 } },
      optional: false,
      ordered: true,
      maxFires: 1,
    },
  ],
  onUnexpectedGate: 'fail_scenario',
};

test('gates: scripted approve resolves immediately with harness:<stepId> attribution', async () => {
  const clock = createSimClock(T0);
  const engine = createGateScriptEngine({ script: SCRIPT, clock, seed: 1 });
  const { calls, fn } = recorder();

  await engine.applyScripts([gate('g1', 'action-approval', { action: 'bind', policyId: 'POL-1' })], fn);
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0]!.wire, { kind: 'approve' });
  assert.equal(calls[0]!.resolvedBy, 'harness:s1');

  // Same gate re-offered on the next drain: already handled, not re-resolved.
  await engine.applyScripts([gate('g1', 'action-approval', { action: 'bind' })], fn);
  assert.equal(calls.length, 1);

  const report = engine.report();
  assert.deepEqual(report.resolutions, [{ gateId: 'g1', stepId: 's1', resolution: 'approve' }]);
  assert.deepEqual(report.neverRaised, ['s2'], 'unfired required step is reported');
  assert.deepEqual(report.unexpected, []);
});

test('gates: unexpected gate is recorded and NOT resolved under fail_scenario', async () => {
  const clock = createSimClock(T0);
  const engine = createGateScriptEngine({ script: SCRIPT, clock, seed: 1 });
  const { calls, fn } = recorder();

  await engine.applyScripts([gate('g9', 'budget-raise', { addUsd: 100 })], fn);
  assert.equal(calls.length, 0, 'fail_scenario leaves the gate open');
  const report = engine.report();
  assert.deepEqual(report.unexpected, [{ gateId: 'g9', kind: 'budget-raise' }]);
});

test('gates: onUnexpectedGate {resolve} resolves with harness:unexpected', async () => {
  const clock = createSimClock(T0);
  const script: ApprovalScript = { ...SCRIPT, onUnexpectedGate: { resolve: { kind: 'reject', reason: 'not in script' } } };
  const engine = createGateScriptEngine({ script, clock, seed: 1 });
  const { calls, fn } = recorder();
  await engine.applyScripts([gate('g9', 'budget-raise', {})], fn);
  assert.equal(calls.length, 1);
  assert.equal(calls[0]!.resolvedBy, 'harness:unexpected');
  assert.deepEqual(calls[0]!.wire, { kind: 'reject', reason: 'not in script' });
  assert.deepEqual(engine.report().unexpected, [{ gateId: 'g9', kind: 'budget-raise' }]);
});

test('gates: ordered steps must match in order — out-of-order gate is unexpected', async () => {
  const clock = createSimClock(T0);
  const engine = createGateScriptEngine({ script: SCRIPT, clock, seed: 1 });
  const { calls, fn } = recorder();
  // s2's gate arrives while s1 (required, ordered) is still unmatched.
  await engine.applyScripts([gate('g2', 'input-request', {})], fn);
  assert.equal(calls.length, 0);
  assert.deepEqual(engine.report().unexpected, [{ gateId: 'g2', kind: 'input-request' }]);
});

test('gates: afterSim schedules the resolution on the sim timeline', async () => {
  const clock = createSimClock(T0);
  const script: ApprovalScript = {
    mode: 'scripted',
    steps: [{
      id: 's1', expect: { kind: 'action-approval' }, resolve: { kind: 'approve' },
      afterSim: '1d', optional: false, ordered: true, maxFires: 1,
    }],
    onUnexpectedGate: 'fail_scenario',
  };
  const engine = createGateScriptEngine({ script, clock, seed: 1 });
  const { calls, fn } = recorder();

  await engine.applyScripts([gate('g1', 'action-approval', {})], fn);
  assert.equal(calls.length, 0, 'not resolved yet — simulated human latency');
  const at = engine.nextResolutionAt();
  assert.ok(at);
  assert.equal(at.toISOString(), new Date(T0.getTime() + DAY).toISOString());

  await engine.resolveDue(new Date(T0.getTime() + DAY / 2), fn);
  assert.equal(calls.length, 0, 'not due yet');
  await engine.resolveDue(at, fn);
  assert.equal(calls.length, 1);
  assert.equal(engine.nextResolutionAt(), null);
  assert.deepEqual(engine.report().resolutions, [{ gateId: 'g1', stepId: 's1', resolution: 'approve' }]);
});

test('gates: auto_approve stops at maxGates so the deadlock can surface', async () => {
  const clock = createSimClock(T0);
  const engine = createGateScriptEngine({ script: { mode: 'auto_approve', maxGates: 1 }, clock, seed: 1 });
  const { calls, fn } = recorder();
  await engine.applyScripts([gate('g1', 'action-approval', {}), gate('g2', 'action-approval', {})], fn);
  assert.equal(calls.length, 1, 'second gate is left unresolved');
  assert.equal(calls[0]!.resolvedBy, 'harness:auto');
});

test('gates: auto_reject rejects everything with the scripted reason', async () => {
  const clock = createSimClock(T0);
  const engine = createGateScriptEngine({ script: { mode: 'auto_reject', reason: 'nope' }, clock, seed: 1 });
  const { calls, fn } = recorder();
  await engine.applyScripts([gate('g1', 'plan-approval', {})], fn);
  assert.deepEqual(calls[0]!.wire, { kind: 'reject', reason: 'nope' });
});
