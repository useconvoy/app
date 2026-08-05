/**
 * Hand-built fixture builders for grader/scoring tests. No dependency on
 * src/sandbox implementations or src/executors — records are constructed
 * in-test, which is exactly the grader-of-graders discipline: every assertion
 * kind gets a fixture where it passes and a violator where it must fail.
 */

import { createHash } from 'node:crypto';
import { EventSchema, type ConvoyEvent } from '../src/runtime/events.ts';
import type {
  GateScriptReport,
  GradeRecord,
  WorldBundle,
  WorldFile,
  WorldMessage,
  WorldRecord,
} from '../src/sandbox/api.ts';
import type { AnswerKey, Scenario } from '../src/schema/scenario.ts';
import { makeBundleQuery } from '../src/graders/world-query.ts';

export const T0 = Date.parse('2026-01-05T00:00:00.000Z');

/** Sim timestamp helper: hours after T0. */
export function at(hours: number): string {
  return new Date(T0 + hours * 3_600_000).toISOString();
}

export function sha(content: string): string {
  return createHash('sha256').update(content).digest('hex');
}

/** Build a schema-validated event. Base fields default; pass overrides last. */
export function ev(seq: number, ts: string, fields: Record<string, unknown>): ConvoyEvent {
  return EventSchema.parse({
    eventId: `e${seq}`,
    missionId: 'm1',
    seq,
    ts,
    wallTs: ts,
    ...fields,
  });
}

export function wfile(id: string, name: string, mime: string, content: string): WorldFile {
  return { id, name, mime, hash: sha(content), content };
}

export function wrec(collection: string, id: string, fields: Record<string, unknown>): WorldRecord {
  return { collection, id, fields, updatedAt: at(0) };
}

let msgCounter = 0;
export function wmsg(partial: Partial<WorldMessage> & Pick<WorldMessage, 'direction'>): WorldMessage {
  msgCounter += 1;
  return {
    id: `msg-${msgCounter}`,
    threadId: 'thread-1',
    from: 'agent@convoy.test',
    to: [],
    subject: '',
    body: '',
    attachments: [],
    ts: at(0),
    ...partial,
  };
}

export function makeBundle(partial: Partial<Omit<WorldBundle, 'hash'>> = {}): WorldBundle {
  return {
    hash: 'bundle-hash',
    messages: partial.messages ?? [],
    records: partial.records ?? [],
    files: partial.files ?? [],
  };
}

export function emptyGateReport(): GateScriptReport {
  return { neverRaised: [], unexpected: [], resolutions: [] };
}

export function makeScenario(overrides: Partial<Scenario> = {}): Scenario {
  return {
    id: 'scn-test',
    title: 'test scenario',
    missionType: 'renewal',
    kind: 'single',
    fixture: { pack: 'pack-a', packHash: 'ph-a' },
    t0: '2026-01-05',
    seed: 42,
    bindings: {},
    trigger: { kind: 'api', missionSpec: { missionType: 'renewal', goal: 'renew the policy' } },
    counterparties: [],
    approvals: { mode: 'auto_approve', maxGates: 10 },
    budgets: { usd: 10, simTime: '45d', wallClock: '10m', onExhaustion: 'fail' },
    answerKeyRef: { path: 'keys/scn-test.json', hash: 'kh' },
    graders: [],
    provenance: { kind: 'authored' },
    tags: [],
    ...overrides,
  };
}

export function makeKey(overrides: Partial<AnswerKey> = {}): AnswerKey {
  return {
    scenarioId: 'scn-test',
    facts: {},
    ...overrides,
  };
}

export interface RecordParts {
  scenario?: Scenario;
  answerKey?: AnswerKey;
  events?: ConvoyEvent[];
  world?: WorldBundle;
  gateReport?: GateScriptReport;
}

export function makeRecord(parts: RecordParts = {}): GradeRecord {
  const world = parts.world ?? makeBundle();
  return {
    scenario: parts.scenario ?? makeScenario(),
    answerKey: parts.answerKey ?? makeKey(),
    events: parts.events ?? [],
    world,
    worldQuery: makeBundleQuery(world),
    gateReport: parts.gateReport ?? emptyGateReport(),
  };
}
