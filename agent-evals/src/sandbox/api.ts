/**
 * Sandbox interfaces — the contract between the DES driver, the world +
 * emulators, the counterparty engine, the gate-script engine, and whatever
 * executor is being evaluated (scripted golden/violator, the frozen baseline,
 * or Aneesh's real runtime).
 *
 * The property that keeps rehearsals honest: the executor cannot tell sim from
 * prod. It sees a ClockPort, a ToolGateway, and gates — nothing else.
 */

import type { ClockPort, OpenGate, RuntimeClient } from '../runtime/ports.ts';
import type { ConvoyEvent, MissionId } from '../runtime/events.ts';
import type { EventLog } from '../runtime/log.ts';
import type { CounterpartyScript, GateResolution, Scenario } from '../schema/scenario.ts';

// ---------------------------------------------------------------------------
// Clock
// ---------------------------------------------------------------------------

/** Harness-owned sim clock. Sole authority on sim time; only runUntil advances it. */
export interface SimClock extends ClockPort {
  advanceTo(t: Date): void;
}

// ---------------------------------------------------------------------------
// World
// ---------------------------------------------------------------------------

export interface WorldMessage {
  id: string;
  threadId: string;
  from: string;
  to: string[];
  subject: string;
  body: string;
  attachments: Array<{ name: string; fileId: string }>;
  /** Domain-time ISO timestamp. */
  ts: string;
  direction: 'outbound' | 'inbound'; // relative to the agent
}

export interface WorldRecord {
  collection: string; // 'policy' | 'portal_request' | 'contact' | ...
  id: string;
  fields: Record<string, unknown>;
  updatedAt: string;
}

export interface WorldFile {
  id: string;
  name: string;
  mime: string;
  /** Content hash (sha256 hex). */
  hash: string;
  content: string;
}

export type WorldEvent =
  | { kind: 'message_sent'; message: WorldMessage }
  | { kind: 'message_delivered'; message: WorldMessage }
  | { kind: 'record_changed'; record: WorldRecord }
  | { kind: 'portal_transition'; requestId: string; to: string };

/**
 * The in-process world state one sandbox instance owns. Mutations emit
 * WorldEvent on the bus (consumed by the counterparty engine). Content is
 * exportable as a content-addressed bundle at teardown for end-state graders.
 */
export interface WorldStore {
  seedFromPack(packDir: string, t0: Date, seed: number): void;

  sendMessage(m: Omit<WorldMessage, 'id' | 'ts'>): WorldMessage;
  deliverMessage(m: Omit<WorldMessage, 'id' | 'ts'>): WorldMessage;
  listMessages(filter?: { direction?: 'outbound' | 'inbound'; toContains?: string; threadId?: string }): WorldMessage[];

  upsertRecord(collection: string, id: string, fields: Record<string, unknown>): WorldRecord;
  getRecord(collection: string, id: string): WorldRecord | undefined;
  listRecords(collection: string): WorldRecord[];

  putFile(f: Omit<WorldFile, 'id' | 'hash'>): WorldFile;
  getFile(id: string): WorldFile | undefined;
  /** Fixture-pack path lookup ("attachments/loss-runs-2024.txt" → file). */
  fileByPackPath(path: string): WorldFile | undefined;

  onEvent(handler: (e: WorldEvent) => void): void;

  /**
   * Read a value by query path for graders/assertions, e.g.
   * "records.policy.POL-1042.renewal_status", "messages.sent.*.to",
   * "files.*.name". Returns all matches.
   */
  query(q: string): unknown[];

  /** Content-addressed export written at teardown; what end-state graders read. */
  exportBundle(): WorldBundle;
}

export interface WorldBundle {
  hash: string;
  messages: WorldMessage[];
  records: WorldRecord[];
  files: WorldFile[];
}

// ---------------------------------------------------------------------------
// Tool gateway — the only way any executor touches the world.
// ---------------------------------------------------------------------------

export interface ToolCallCtx {
  missionId: MissionId;
  itemRef?: string;
  stepId?: string;
  /**
   * Caller-minted idempotency key. Two invokes with the SAME key dedupe (crash
   * replay never re-fires a side effect); omitting it mints a fresh key, so an
   * intentional retry (re-requesting a corrected document) re-executes.
   */
  idempotencyKey?: string;
}

/**
 * Governed tool surface. Emulator bindings implement the tools; the gateway
 * enforces the two-phase envelope (effectful tools), collapsed events (pure
 * tools), idempotency-key dedupe, and budget metering — and records every
 * call in the event log. Approval gates for effectful tools are raised by the
 * EXECUTOR side (runtime policy), not by the gateway, in v1.
 */
export interface ToolGateway {
  invoke(tool: string, args: unknown, ctx: ToolCallCtx): Promise<unknown>;
  /** Tool manifest hash — input to the certified tuple. */
  manifestHash(): string;
}

/** One tool's emulator implementation. */
export interface ToolEmulator {
  tool: string;
  effectful: boolean;
  handler(args: unknown, world: WorldStore, ctx: ToolCallCtx): Promise<unknown> | unknown;
}

// ---------------------------------------------------------------------------
// Sandbox instance + DES driver
// ---------------------------------------------------------------------------

export type StopCondition =
  | { kind: 'terminal' }
  | { kind: 'gate-open' }
  | { kind: 'sim-time'; at: Date };

export interface RunReport {
  terminal: 'landed' | 'cancelled' | 'failed' | null;
  deadlock: boolean;
  deadlockDiagnosis?: string;
  guardTripped: 'wall' | 'usd' | 'sim' | null;
  openGates: OpenGate[];
  simNow: Date;
  wallMs: number;
  stepsExecuted: number;
}

export interface GateScriptReport {
  /** Non-optional scripted steps that never matched a raised gate. */
  neverRaised: string[];
  /** Gates resolved by onUnexpectedGate. */
  unexpected: Array<{ gateId: string; kind: string }>;
  resolutions: Array<{ gateId: string; stepId: string | 'auto' | 'unexpected'; resolution: GateResolution['kind'] }>;
}

export interface SandboxInstance {
  readonly id: string;
  readonly environmentId: string;
  readonly clock: SimClock;
  readonly world: WorldStore;
  readonly log: EventLog;
  readonly missionId: MissionId;

  /** The DES driver: drain → apply gate scripts → advance to next event → deliver. */
  runUntil(stop: StopCondition): Promise<RunReport>;
  /** One drain+advance cycle, for debugging. */
  step(): Promise<RunReport>;

  gateReport(): GateScriptReport;
  destroy(): { events: ConvoyEvent[]; world: WorldBundle };
}

/**
 * The runtime under evaluation is constructed AFTER the sandbox exists (it
 * needs the instance's gateway/log/world/clock injected), so the sandbox takes
 * a factory. The real runtime's factory ignores `env` and returns a client
 * over Aneesh's drain(); the ScriptedRuntime factory wires the coroutine
 * scheduler to this instance's surfaces.
 */
export type RuntimeFactory = (env: {
  gateway: ToolGateway;
  log: EventLog;
  world: WorldStore;
  clock: SimClock;
}) => RuntimeClient;

export interface SandboxService {
  /**
   * Build a live instance for a scenario: seed world from the fixture pack,
   * expand counterparty profiles, register emulator bindings, start the
   * mission on the runtime the factory returns.
   */
  create(scenario: Scenario, runtimeFactory: RuntimeFactory, opts?: { packDir?: string }): Promise<SandboxInstance>;
}

/** Everything graders may see. NOTE: no transcript, no model text — by type. */
export interface GradeRecord {
  scenario: Scenario;
  answerKey: import('../schema/scenario.ts').AnswerKey;
  events: ConvoyEvent[];
  world: WorldBundle;
  /** Live query access when grading right after a run; bundle-backed in replay. */
  worldQuery: (q: string) => unknown[];
  gateReport: GateScriptReport;
}

// ---------------------------------------------------------------------------
// Scripted executors (golden / violator / baseline) — cooperative coroutines
// scheduled by the ScriptedRuntime, which implements RuntimeClient so the
// harness drives it exactly as it will drive Aneesh's runtime.
// ---------------------------------------------------------------------------

export interface ExecutorCtx {
  missionId: MissionId;
  clock: ClockPort;
  gateway: ToolGateway;
  log: EventLog;
  goal: string;
  params: Record<string, unknown>;

  /** Park until sim time ≥ now + duration (registers a durable timer). */
  wait(durationMs: number): Promise<void>;
  /** Park until an inbound message matching the predicate exists (checked each drain). */
  awaitInbound(match: { toContains?: string; subjectRegex?: string; afterTs?: string }): Promise<WorldMessage>;
  /** Raise a gate and park until it is resolved. */
  raiseGate(kind: 'action-approval' | 'input-request' | 'plan-approval' | 'budget-raise', payload: unknown, opts?: { stepTag?: string; itemRef?: string }): Promise<{ resolution: string; payload?: unknown }>;
  /** Record an artifact (content goes to the world file store; event to the log). */
  emitArtifact(a: { tag: string; content: string; mime?: string; itemRef?: string }): void;
  land(summary?: string): void;
  fail(reason: string): void;
}

export type ScriptedExecutorFn = (ctx: ExecutorCtx) => Promise<void>;
