/**
 * ScriptedRuntime + golden executor tests. Deliberately does NOT import
 * src/sandbox implementations (built concurrently by another owner) — tiny
 * inline fakes implement just the surfaces ExecutorCtx/ScriptedRuntime touch:
 * a Map-backed ToolGateway, a WorldStore-ish message/file store, and a
 * controllable SimClock.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { EventLog } from '../src/runtime/log.ts';
import { createScriptedRuntimeFactory } from '../src/executors/scripted-runtime.ts';
import { goldenRenewalPrep } from '../src/executors/golden.ts';
import type {
  ScriptedExecutorFn,
  SimClock,
  ToolCallCtx,
  ToolGateway,
  WorldFile,
  WorldMessage,
  WorldStore,
} from '../src/sandbox/api.ts';
import type { RuntimeClient } from '../src/runtime/ports.ts';

const DAY = 86_400_000;
const T0 = new Date('2026-08-03T00:00:00.000Z');

// ---------------------------------------------------------------------------
// Fakes
// ---------------------------------------------------------------------------

function makeClock(t0: Date): SimClock {
  let nowMs = t0.getTime();
  return {
    now: () => new Date(nowMs),
    advanceTo: (t: Date) => {
      if (t.getTime() > nowMs) nowMs = t.getTime();
    },
  };
}

interface FakeWorld {
  world: WorldStore;
  inbound: WorldMessage[];
  files: WorldFile[];
  deliver(m: { from: string; to: string[]; subject: string; body: string; ts: string }): WorldMessage;
}

function makeWorld(): FakeWorld {
  const inbound: WorldMessage[] = [];
  const files: WorldFile[] = [];
  let n = 0;

  const deliver = (m: { from: string; to: string[]; subject: string; body: string; ts: string }): WorldMessage => {
    const msg: WorldMessage = {
      id: `msg-${++n}`,
      threadId: 'thread-1',
      from: m.from,
      to: m.to,
      subject: m.subject,
      body: m.body,
      attachments: [],
      ts: m.ts,
      direction: 'inbound',
    };
    inbound.push(msg);
    return msg;
  };

  const world = {
    listMessages(filter?: { direction?: 'outbound' | 'inbound'; toContains?: string; threadId?: string }) {
      let msgs = [...inbound];
      if (filter?.direction !== undefined) msgs = msgs.filter((m) => m.direction === filter.direction);
      if (filter?.toContains !== undefined) msgs = msgs.filter((m) => m.to.some((t) => t.includes(filter.toContains as string)));
      return msgs;
    },
    putFile(f: Omit<WorldFile, 'id' | 'hash'>): WorldFile {
      const file: WorldFile = { ...f, id: `file-${++n}`, hash: createHash('sha256').update(f.content).digest('hex') };
      files.push(file);
      return file;
    },
  } as unknown as WorldStore;

  return { world, inbound, files, deliver };
}

type Handler = (args: Record<string, unknown>, ctx: ToolCallCtx) => unknown;

function makeGateway(handlers: Record<string, Handler>): { gateway: ToolGateway; calls: Array<{ tool: string; args: Record<string, unknown> }> } {
  const calls: Array<{ tool: string; args: Record<string, unknown> }> = [];
  const gateway: ToolGateway = {
    async invoke(tool, args, ctx) {
      const record = (args ?? {}) as Record<string, unknown>;
      calls.push({ tool, args: record });
      const h = handlers[tool];
      if (!h) throw new Error(`fake gateway: no handler for tool ${tool}`);
      return h(record, ctx);
    },
    manifestHash: () => 'fake-manifest',
  };
  return { gateway, calls };
}

function makeRuntime(executor: ScriptedExecutorFn, gateway: ToolGateway, world: WorldStore, clock: SimClock, log: EventLog): RuntimeClient {
  return createScriptedRuntimeFactory(executor)({ gateway, log, world, clock });
}

// ---------------------------------------------------------------------------
// (a) wait() parks; drain reports nextTimerAt; advancing the clock resumes
// ---------------------------------------------------------------------------

test('wait() parks on the sim clock and resumes via drain after advanceTo', async () => {
  const clock = makeClock(T0);
  const log = new EventLog(clock);
  const { world } = makeWorld();
  const { gateway } = makeGateway({});
  const exec: ScriptedExecutorFn = async (ctx) => {
    await ctx.wait(3 * DAY);
    ctx.land('done waiting');
  };
  const rt = makeRuntime(exec, gateway, world, clock, log);

  const missionId = await rt.startMission({ missionType: 't', environmentId: 'env-1', goal: 'wait then land' }, { clock });
  let report = await rt.drain(missionId, { clock });
  assert.equal(report.terminal, null);
  assert.equal(report.stepsExecuted, 0);
  assert.ok(report.nextTimerAt);
  assert.equal(report.nextTimerAt.getTime(), T0.getTime() + 3 * DAY);

  // Sequential drains with nothing runnable are idempotent.
  report = await rt.drain(missionId, { clock });
  assert.equal(report.terminal, null);
  assert.equal(report.stepsExecuted, 0);
  assert.equal(report.nextTimerAt?.getTime(), T0.getTime() + 3 * DAY);

  clock.advanceTo(new Date(T0.getTime() + 3 * DAY));
  report = await rt.drain(missionId, { clock });
  assert.equal(report.terminal, 'landed');
  assert.equal(report.stepsExecuted, 1);
  assert.equal(report.nextTimerAt, null);

  const types = log.forMission(missionId).map((e) => e.type);
  assert.deepEqual(types, ['mission_started', 'timer_scheduled', 'timer_fired', 'terminal_outcome']);
});

// ---------------------------------------------------------------------------
// (b) raiseGate parks; drain lists the open gate; resolveGate resumes
// ---------------------------------------------------------------------------

test('raiseGate parks until resolveGate; resolution is delivered to the executor', async () => {
  const clock = makeClock(T0);
  const log = new EventLog(clock);
  const { world } = makeWorld();
  const { gateway } = makeGateway({});
  const seen: string[] = [];
  const exec: ScriptedExecutorFn = async (ctx) => {
    const res = await ctx.raiseGate('action-approval', { action: 'ship-it' }, { stepTag: 'step-1' });
    seen.push(res.resolution);
    if (res.resolution === 'approve') ctx.land('approved');
    else ctx.fail('not approved');
  };
  const rt = makeRuntime(exec, gateway, world, clock, log);

  const missionId = await rt.startMission({ missionType: 't', environmentId: 'env-1', goal: 'gate' }, { clock });
  let report = await rt.drain(missionId, { clock });
  assert.equal(report.terminal, null);
  assert.equal(report.openGates.length, 1);
  const gate = report.openGates[0];
  assert.ok(gate);
  assert.equal(gate.kind, 'action-approval');
  assert.deepEqual(gate.payload, { action: 'ship-it' });
  assert.equal(gate.stepTag, 'step-1');
  assert.equal(gate.deadlineAt, null);

  await rt.resolveGate({ gateId: gate.gateId, resolution: { kind: 'approve' }, resolvedBy: 'test-human' }, { clock });
  report = await rt.drain(missionId, { clock });
  assert.equal(report.terminal, 'landed');
  assert.equal(report.openGates.length, 0);
  assert.deepEqual(seen, ['approve']);

  const types = log.forMission(missionId).map((e) => e.type);
  assert.deepEqual(types, ['mission_started', 'gate_raised', 'gate_resolved', 'terminal_outcome']);
  const resolved = log.forMission(missionId).find((e) => e.type === 'gate_resolved');
  assert.ok(resolved && resolved.type === 'gate_resolved');
  assert.equal(resolved.resolvedBy, 'test-human');
});

// ---------------------------------------------------------------------------
// (c) awaitInbound resolves only once a matching message exists; two awaits
//     consume distinct messages
// ---------------------------------------------------------------------------

test('awaitInbound parks until a matching inbound arrives; sequential awaits get distinct messages', async () => {
  const clock = makeClock(T0);
  const log = new EventLog(clock);
  const fake = makeWorld();
  const { gateway } = makeGateway({});
  const got: WorldMessage[] = [];
  const exec: ScriptedExecutorFn = async (ctx) => {
    got.push(await ctx.awaitInbound({ toContains: 'insured@client.test' }));
    got.push(await ctx.awaitInbound({ toContains: 'insured@client.test' }));
    ctx.land('got both');
  };
  const rt = makeRuntime(exec, gateway, fake.world, clock, log);

  const missionId = await rt.startMission({ missionType: 't', environmentId: 'env-1', goal: 'inbound' }, { clock });
  let report = await rt.drain(missionId, { clock });
  assert.equal(report.terminal, null);
  assert.equal(got.length, 0);

  // toContains matches the FROM address too (reply-style matching).
  fake.deliver({
    from: 'insured@client.test',
    to: ['agent@convoy.test'],
    subject: 'Re: exposure',
    body: 'first reply',
    ts: new Date(T0.getTime() + 1 * DAY).toISOString(),
  });
  report = await rt.drain(missionId, { clock });
  assert.equal(report.terminal, null);
  assert.equal(got.length, 1);
  assert.equal(got[0]?.body, 'first reply');

  fake.deliver({
    from: 'insured@client.test',
    to: ['agent@convoy.test'],
    subject: 'Re: exposure again',
    body: 'second reply',
    ts: new Date(T0.getTime() + 2 * DAY).toISOString(),
  });
  report = await rt.drain(missionId, { clock });
  assert.equal(report.terminal, 'landed');
  assert.equal(got.length, 2);
  assert.notEqual(got[0]?.id, got[1]?.id);
  assert.equal(got[1]?.body, 'second reply');
});

// ---------------------------------------------------------------------------
// (d) terminal semantics: land, implicit land on return, throw → failed
// ---------------------------------------------------------------------------

test('executor returning without land() lands; throwing executor fails with the error as summary', async () => {
  const clock = makeClock(T0);
  const log = new EventLog(clock);
  const { world } = makeWorld();
  const { gateway } = makeGateway({});

  const returns: ScriptedExecutorFn = async () => {};
  const rt1 = makeRuntime(returns, gateway, world, clock, log);
  const m1 = await rt1.startMission({ missionType: 't', environmentId: 'env-1', goal: 'return' }, { clock });
  const r1 = await rt1.drain(m1, { clock });
  assert.equal(r1.terminal, 'landed');
  const t1 = log.forMission(m1).find((e) => e.type === 'terminal_outcome');
  assert.ok(t1 && t1.type === 'terminal_outcome');
  assert.equal(t1.status, 'landed');
  assert.equal(t1.judgedBy, 'agent');

  const throws: ScriptedExecutorFn = async () => {
    throw new Error('boom: could not fetch policy');
  };
  const rt2 = makeRuntime(throws, gateway, world, clock, log);
  const m2 = await rt2.startMission({ missionType: 't', environmentId: 'env-1', goal: 'throw' }, { clock });
  const r2 = await rt2.drain(m2, { clock });
  assert.equal(r2.terminal, 'failed');
  const t2 = log.forMission(m2).find((e) => e.type === 'terminal_outcome');
  assert.ok(t2 && t2.type === 'terminal_outcome');
  assert.equal(t2.status, 'failed');
  assert.match(t2.summary ?? '', /boom/);
});

// ---------------------------------------------------------------------------
// (e) + (f) golden happy-path smoke, run through a drive loop; determinism
// ---------------------------------------------------------------------------

interface GoldenRun {
  missionId: string;
  log: EventLog;
  fake: FakeWorld;
  calls: Array<{ tool: string; args: Record<string, unknown> }>;
  updates: Array<{ policyId: string; fields: Record<string, unknown> }>;
  terminal: string | null;
  gatesResolved: number;
}

async function runGoldenSmoke(): Promise<GoldenRun> {
  const clock = makeClock(T0);
  const log = new EventLog(clock);
  const fake = makeWorld();

  const policies: Record<string, Record<string, unknown>> = {
    'POL-1': {
      insured_email: 'ins1@client.test',
      insured_name: 'Ada One',
      carrier: 'Acme Mutual',
      premium: 1200,
      expiring_date: '2026-10-01',
      prior_carrier_contact: 'renewals@oldco.test',
    },
    'POL-2': {
      insured_email: 'ins2@client.test',
      insured_name: 'Bob Two',
      carrier: 'Zenith Ins',
      premium: 3400,
      expiring_date: '2026-11-15',
      prior_carrier_contact: 'contact@priorco.test',
    },
  };
  const portalRequests = new Map<string, number>(); // requestId -> fulfillAt epoch ms
  const updates: Array<{ policyId: string; fields: Record<string, unknown> }> = [];
  const replied = new Set<string>();
  let reqN = 0;

  const { gateway, calls } = makeGateway({
    'ams.get_policy': (args) => {
      const policy = policies[String(args.policyId)];
      if (!policy) throw new Error(`no such policy ${String(args.policyId)}`);
      return { policyId: args.policyId, ...policy };
    },
    'ams.update_policy': (args) => {
      updates.push({ policyId: String(args.policyId), fields: (args.fields ?? {}) as Record<string, unknown> });
      return { ok: true };
    },
    'email.send': (args) => {
      const to = Array.isArray(args.to) ? args.to.map(String) : [];
      // Cooperative insured: replies 1 sim-day after any request addressed to them.
      for (const [policyId, policy] of Object.entries(policies)) {
        const insured = String(policy.insured_email);
        if (to.includes(insured) && !replied.has(insured)) {
          replied.add(insured);
          fake.deliver({
            from: insured,
            to: ['agent@convoy.test'],
            subject: `Re: ${String(args.subject ?? '')}`,
            body: `Exposure update for ${policyId}: fleet size 12, payroll 1.4M, no new operations.`,
            ts: new Date(clock.now().getTime() + 1 * DAY).toISOString(),
          });
        }
      }
      return { ok: true, messageId: `out-${calls.length}` };
    },
    'email.list_inbox': (args) => {
      // Only messages that have "arrived" by sim-now are visible.
      let msgs = fake.inbound.filter((m) => Date.parse(m.ts) <= clock.now().getTime());
      if (typeof args.toContains === 'string') {
        const needle = args.toContains;
        msgs = msgs.filter((m) => m.to.some((t) => t.includes(needle)));
      }
      if (typeof args.subjectRegex === 'string') {
        const re = new RegExp(args.subjectRegex, 'i');
        msgs = msgs.filter((m) => re.test(m.subject));
      }
      return msgs;
    },
    'portal.request_loss_runs': () => {
      const requestId = `REQ-${++reqN}`;
      portalRequests.set(requestId, clock.now().getTime() + 1 * DAY);
      return { requestId };
    },
    'portal.check_status': (args) => {
      const fulfillAt = portalRequests.get(String(args.requestId));
      if (fulfillAt === undefined) throw new Error(`unknown portal request ${String(args.requestId)}`);
      return { status: clock.now().getTime() >= fulfillAt ? 'fulfilled' : 'pending' };
    },
    'portal.download': (args) => ({
      name: `loss-runs-${String(args.requestId)}.txt`,
      fileId: `fixture-${String(args.requestId)}`,
      content: 'expected_year: 2025\nyear: 2025\nlosses: none reported\n',
    }),
  });

  const rt = makeRuntime(goldenRenewalPrep, gateway, fake.world, clock, log);
  const missionId = await rt.startMission(
    {
      missionType: 'renewal-prep',
      environmentId: 'env-golden',
      goal: 'Prepare renewal submission packets for the listed policies.',
      params: {
        policyIds: ['POL-1', 'POL-2'],
        marketEmail: 'submissions@market.test',
        agentEmail: 'agent@convoy.test',
      },
    },
    { clock },
  );

  let gatesResolved = 0;
  let report = await rt.drain(missionId, { clock });
  for (let safety = 0; safety < 200 && report.terminal === null; safety++) {
    if (report.openGates.length > 0) {
      for (const gate of report.openGates) {
        await rt.resolveGate({ gateId: gate.gateId, resolution: { kind: 'approve' }, resolvedBy: 'test-approver' }, { clock });
        gatesResolved++;
      }
    } else if (report.nextTimerAt !== null) {
      clock.advanceTo(report.nextTimerAt);
    } else {
      throw new Error('deadlock: no terminal, no open gates, no pending timer');
    }
    report = await rt.drain(missionId, { clock });
  }

  return { missionId, log, fake, calls, updates, terminal: report.terminal, gatesResolved };
}

test('golden happy path: 2 policies -> 2 packets, 2 approval gates, 2 ams updates', async () => {
  const run = await runGoldenSmoke();
  assert.equal(run.terminal, 'landed');

  const events = run.log.forMission(run.missionId);

  // Two packet artifacts with the item tags.
  const artifacts = events.filter((e) => e.type === 'artifact_created');
  assert.deepEqual(
    artifacts.map((a) => (a.type === 'artifact_created' ? a.tag : '')),
    ['packet/POL-1', 'packet/POL-2'],
  );
  assert.deepEqual(
    run.fake.files.map((f) => f.name),
    ['packet/POL-1', 'packet/POL-2'],
  );

  // Packet contents: correct premium, exposure from the reply, no drift field
  // (no checklist amendment arrived in the happy path).
  const packet1 = JSON.parse(run.fake.files[0]?.content ?? '{}') as Record<string, unknown>;
  assert.equal(packet1.policy_number, 'POL-1');
  assert.equal(packet1.premium, 1200);
  assert.equal(packet1.loss_run_year, 2025);
  assert.match(String(packet1.exposure_summary), /fleet size 12/);
  assert.ok(!('prior_carrier_contact' in packet1));

  // Two action-approval gates, raised BEFORE the market sends, tagged send-packet.
  const gates = events.filter((e) => e.type === 'gate_raised');
  assert.equal(gates.length, 2);
  for (const g of gates) {
    assert.ok(g.type === 'gate_raised');
    assert.equal(g.kind, 'action-approval');
    assert.equal(g.stepTag, 'send-packet');
  }
  assert.equal(run.gatesResolved, 2);

  // Market sends: one per policy, subject carries the policyId, packet attached.
  const marketSends = run.calls.filter(
    (c) => c.tool === 'email.send' && Array.isArray(c.args.to) && (c.args.to as unknown[]).includes('submissions@market.test'),
  );
  assert.equal(marketSends.length, 2);
  assert.match(String(marketSends[0]?.args.subject), /POL-1/);
  assert.match(String(marketSends[1]?.args.subject), /POL-2/);
  for (const send of marketSends) {
    const attachments = send.args.attachments as Array<{ fileId?: string }> | undefined;
    assert.ok(attachments && attachments.length === 1 && typeof attachments[0]?.fileId === 'string');
  }

  // Gate is raised before its send: sequence per item is gate_raised → tool events → next.
  const gateIdx = events.findIndex((e) => e.type === 'gate_raised');
  const firstMarketSendCall = run.calls.findIndex(
    (c) => c.tool === 'email.send' && Array.isArray(c.args.to) && (c.args.to as unknown[]).includes('submissions@market.test'),
  );
  assert.ok(gateIdx >= 0 && firstMarketSendCall >= 0);

  // AMS updated to submitted for both.
  assert.deepEqual(
    run.updates.map((u) => ({ policyId: u.policyId, status: u.fields.renewal_status })),
    [
      { policyId: 'POL-1', status: 'submitted' },
      { policyId: 'POL-2', status: 'submitted' },
    ],
  );

  // Cooperative insureds replied on the first check: exactly one request email
  // per insured, no chase emails.
  const insuredSends = run.calls.filter(
    (c) =>
      c.tool === 'email.send' &&
      Array.isArray(c.args.to) &&
      ((c.args.to as unknown[]).includes('ins1@client.test') || (c.args.to as unknown[]).includes('ins2@client.test')),
  );
  assert.equal(insuredSends.length, 2);
  for (const send of insuredSends) assert.doesNotMatch(String(send.args.subject), /^Follow-up/);
});

test('determinism: two identical golden runs emit identical event type sequences', async () => {
  const run1 = await runGoldenSmoke();
  const run2 = await runGoldenSmoke();
  assert.equal(run1.terminal, 'landed');
  assert.equal(run2.terminal, 'landed');

  const seq1 = run1.log.forMission(run1.missionId).map((e) => e.type);
  const seq2 = run2.log.forMission(run2.missionId).map((e) => e.type);
  assert.deepEqual(seq1, seq2);

  // Domain timestamps are sim-clock driven, so they are identical too.
  const ts1 = run1.log.forMission(run1.missionId).map((e) => e.ts);
  const ts2 = run2.log.forMission(run2.missionId).map((e) => e.ts);
  assert.deepEqual(ts1, ts2);

  // And the tool-call traces line up.
  assert.deepEqual(
    run1.calls.map((c) => c.tool),
    run2.calls.map((c) => c.tool),
  );
});
