/**
 * Sandbox core tests: SimClock monotonicity, WorldStore seeding/query/export,
 * ToolGateway two-phase envelope + idempotency dedupe.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { EventLog } from '../src/runtime/log.ts';
import { createSimClock } from '../src/sandbox/clock.ts';
import { createWorldStore } from '../src/sandbox/world.ts';
import { createToolGateway } from '../src/sandbox/gateway.ts';
import { emailEmulator } from '../src/sandbox/emulators/email.ts';
import { calendarEmulator } from '../src/sandbox/emulators/calendar.ts';
import type { ToolEmulator, WorldEvent } from '../src/sandbox/api.ts';

const HERE = dirname(fileURLToPath(import.meta.url));
const PACK_MINI = join(HERE, 'fixtures', 'pack-mini');
const T0 = new Date('2026-08-01T00:00:00Z');
const DAY = 86_400_000;

// ---------------------------------------------------------------------------
// (a) clock
// ---------------------------------------------------------------------------

test('clock: starts at t0 and advances forward', () => {
  const clock = createSimClock(T0);
  assert.equal(clock.now().toISOString(), '2026-08-01T00:00:00.000Z');
  clock.advanceTo(new Date(T0.getTime() + 3 * DAY));
  assert.equal(clock.now().toISOString(), '2026-08-04T00:00:00.000Z');
  clock.advanceTo(clock.now()); // same instant is fine
});

test('clock: advancing backwards throws (monotonic)', () => {
  const clock = createSimClock(T0);
  clock.advanceTo(new Date(T0.getTime() + DAY));
  assert.throws(() => clock.advanceTo(T0), /monotonic/);
});

test('clock: now() returns a copy — mutating it does not move the clock', () => {
  const clock = createSimClock(T0);
  clock.now().setFullYear(1999);
  assert.equal(clock.now().toISOString(), T0.toISOString());
});

// ---------------------------------------------------------------------------
// (b) world seeding + query
// ---------------------------------------------------------------------------

test('world: seeds pack with {{t0+3d}} materialization in records, files, threads', () => {
  const clock = createSimClock(T0);
  const world = createWorldStore(clock, 42);
  world.seedFromPack(PACK_MINI, T0, 42);

  assert.deepEqual(world.query('records.policy.POL-1.expiring_date'), ['2026-08-04']);
  assert.deepEqual(world.query('records.policy.POL-1.bound_on'), ['2025-08-06']);
  assert.deepEqual(world.query('records.policy.POL-2.expiring_date'), ['2026-09-10']);

  const lossRuns = world.fileByPackPath('attachments/loss-runs.txt');
  assert.ok(lossRuns, 'file resolvable by pack path');
  assert.match(lossRuns.content, /Report date: 2026-08-01/);
  assert.match(lossRuns.content, /to 2026-08-04/);
  assert.equal(lossRuns.hash, createHash('sha256').update(lossRuns.content, 'utf8').digest('hex'));

  const names = world.query('files.*.name');
  assert.ok(names.includes('loss-runs.txt') && names.includes('acord-125.txt'));

  const inbound = world.listMessages({ direction: 'inbound' });
  assert.equal(inbound.length, 1);
  assert.match(inbound[0]!.body, /expires on 2026-08-04/);
  assert.equal(inbound[0]!.ts, '2026-07-31');
  assert.deepEqual(world.query('messages.inbound.*.from'), ['maria@acme-robotics.com']);
});

test('world: mutations emit events; sent vs delivered directions; upsert merges', () => {
  const clock = createSimClock(T0);
  const world = createWorldStore(clock, 1);
  const seen: WorldEvent[] = [];
  world.onEvent((e) => seen.push(e));

  const out = world.sendMessage({
    threadId: 't1', from: 'agent@convoy.sim', to: ['x@y.z'], subject: 's', body: 'b',
    attachments: [], direction: 'outbound',
  });
  assert.equal(out.direction, 'outbound');
  assert.equal(out.ts, T0.toISOString());
  const inb = world.deliverMessage({
    threadId: 't1', from: 'x@y.z', to: ['agent@convoy.sim'], subject: 're: s', body: 'b2',
    attachments: [], direction: 'inbound',
  });
  assert.equal(inb.direction, 'inbound');
  world.upsertRecord('policy', 'P', { a: 1 });
  world.upsertRecord('policy', 'P', { b: 2 });
  assert.deepEqual(world.getRecord('policy', 'P')!.fields, { a: 1, b: 2 });

  assert.deepEqual(seen.map((e) => e.kind), ['message_sent', 'message_delivered', 'record_changed', 'record_changed']);
  assert.deepEqual(world.query('messages.sent.*.to'), [['x@y.z']]);
});

test('world: exportBundle hash is stable across identical worlds and moves on change', () => {
  const build = () => {
    const world = createWorldStore(createSimClock(T0), 42);
    world.seedFromPack(PACK_MINI, T0, 42);
    return world;
  };
  const a = build();
  const b = build();
  assert.equal(a.exportBundle().hash, b.exportBundle().hash);
  b.upsertRecord('policy', 'POL-1', { renewal_status: 'bound' });
  assert.notEqual(a.exportBundle().hash, b.exportBundle().hash);
});

// ---------------------------------------------------------------------------
// (c) gateway: two-phase events + idempotency dedupe
// ---------------------------------------------------------------------------

function gatewayRig(bindings: Record<string, { kind: 'emulator' } | { kind: 'replay'; cassette: string; miss: 'fail' }> = {}) {
  const clock = createSimClock(T0);
  const world = createWorldStore(clock, 1);
  const log = new EventLog(clock);
  let effectCalls = 0;
  let pureCalls = 0;
  const emulators: ToolEmulator[] = [
    { tool: 'test.effect', effectful: true, handler: () => ({ ok: ++effectCalls }) },
    { tool: 'test.read', effectful: false, handler: () => ({ n: ++pureCalls }) },
    { tool: 'test.boom', effectful: false, handler: () => { throw new Error('kaboom'); } },
  ];
  const gateway = createToolGateway({ emulators, bindings: bindings as never, log, world });
  return { gateway, log, world, effectCallCount: () => effectCalls };
}

const CTX = { missionId: 'm1' };

test('gateway: pure tool emits one collapsed tool_call + a budget_debit', async () => {
  const { gateway, log } = gatewayRig();
  const result = await gateway.invoke('test.read', { q: 1 }, CTX);
  assert.deepEqual(result, { n: 1 });
  const events = log.forMission('m1');
  assert.deepEqual(events.map((e) => e.type), ['tool_call', 'budget_debit']);
  assert.equal(events[0]!.type === 'tool_call' && (events[0] as { result?: unknown }).result !== undefined, true);
});

test('gateway: pure tool error is recorded on the collapsed event and rethrown', async () => {
  const { gateway, log } = gatewayRig();
  await assert.rejects(() => gateway.invoke('test.boom', {}, CTX), /kaboom/);
  const call = log.forMission('m1').find((e) => e.type === 'tool_call')!;
  assert.equal((call as { error?: string }).error, 'kaboom');
});

test('gateway: effectful two-phase envelope + dedupe (handler runs once, 8 events not 10)', async () => {
  const { gateway, log, effectCallCount } = gatewayRig();
  // Caller-minted key models crash replay: the runtime re-fires the SAME
  // attempt with the SAME key, and the side effect must not run twice.
  const replayCtx = { ...CTX, idempotencyKey: 'attempt-1' };
  const first = await gateway.invoke('test.effect', { policyId: 'POL-1' }, replayCtx);
  const second = await gateway.invoke('test.effect', { policyId: 'POL-1' }, replayCtx);

  assert.equal(effectCallCount(), 1, 'handler must not re-run on idempotent replay');
  assert.deepEqual(second, first, 'replay returns the recorded result');

  const events = log.forMission('m1');
  assert.equal(events.length, 8);
  assert.deepEqual(
    events.map((e) => e.type),
    ['tool_intent', 'tool_approved', 'tool_executed', 'tool_result', 'budget_debit',
     'tool_intent', 'tool_result', 'budget_debit']
  );
  const keys = events
    .filter((e) => e.type === 'tool_intent')
    .map((e) => (e as { idempotencyKey: string }).idempotencyKey);
  assert.equal(keys[0], keys[1], 'caller-minted key is carried on both intents');

  // No caller key → fresh key per invoke → an INTENTIONAL retry re-executes.
  await gateway.invoke('test.effect', { policyId: 'POL-1' }, CTX);
  await gateway.invoke('test.effect', { policyId: 'POL-1' }, CTX);
  assert.equal(effectCallCount(), 3, 'intentional retries (no caller key) re-run the handler');
});

test('gateway: unknown tool and non-emulator bindings throw', async () => {
  const { gateway } = gatewayRig({ 'test.effect': { kind: 'replay', cassette: 'c', miss: 'fail' } });
  await assert.rejects(() => gateway.invoke('nope.tool', {}, CTX), /unknown tool/);
  await assert.rejects(() => gateway.invoke('test.effect', {}, CTX), /not implemented in v1/);
});

test('gateway: manifestHash is order-independent over tools + effectful flags', () => {
  const clock = createSimClock(T0);
  const world = createWorldStore(clock, 1);
  const a: ToolEmulator = { tool: 'a', effectful: true, handler: () => 0 };
  const b: ToolEmulator = { tool: 'b', effectful: false, handler: () => 0 };
  const g1 = createToolGateway({ emulators: [a, b], bindings: {}, log: new EventLog(clock), world });
  const g2 = createToolGateway({ emulators: [b, a], bindings: {}, log: new EventLog(clock), world });
  assert.equal(g1.manifestHash(), g2.manifestHash());
  const bEff: ToolEmulator = { tool: 'b', effectful: true, handler: () => 0 };
  const g3 = createToolGateway({ emulators: [a, bEff], bindings: {}, log: new EventLog(clock), world });
  assert.notEqual(g1.manifestHash(), g3.manifestHash());
});

test('gateway: emulators operate on the world (email round-trip, sim-time today)', async () => {
  const clock = createSimClock(T0);
  const world = createWorldStore(clock, 1);
  world.seedFromPack(PACK_MINI, T0, 1);
  const log = new EventLog(clock);
  const gateway = createToolGateway({ emulators: [...emailEmulator, ...calendarEmulator], bindings: {}, log, world });

  const today = (await gateway.invoke('calendar.today', {}, CTX)) as { today: string };
  assert.equal(today.today, '2026-08-01');

  const file = world.fileByPackPath('attachments/acord-125.txt')!;
  await gateway.invoke(
    'email.send',
    { to: 'carrier@acme.sim', subject: 'Loss runs request', body: 'Please send loss runs.', attachments: [file.id] },
    CTX
  );
  const sent = world.listMessages({ direction: 'outbound' });
  assert.equal(sent.length, 1);
  assert.deepEqual(sent[0]!.attachments.map((x) => x.name), ['acord-125.txt']);

  const inbox = (await gateway.invoke('email.list_inbox', {}, CTX)) as { messages: Array<{ messageId: string }> };
  assert.equal(inbox.messages.length, 1); // the seeded kickoff thread
  const read = (await gateway.invoke('email.read', { messageId: inbox.messages[0]!.messageId }, CTX)) as { body: string };
  assert.match(read.body, /2026-08-04/);
});
