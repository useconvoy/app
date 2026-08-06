/**
 * The co-signed runtime seam.
 *
 * These are the ONLY things agent-evals asks of agent-runtime. Everything else
 * the harness needs it gets by reading the event log directly. This seam is
 * co-signed: widening it changes what agent-runtime must guarantee, so changes
 * here need sign-off from both owning teams.
 *
 * Design rule that makes the whole sandbox work: DOMAIN TIME (event ts, gate
 * deadlines, durable-timer fire_at, retry backoff, the "today is..." string in
 * prompts) flows through ClockPort. MECHANICAL TIME (leases, heartbeats,
 * HTTP/LLM timeouts) stays on the wall clock, in sim too — a crashed process is
 * really crashed even when the calendar is fake.
 */

import type { GateKind, MissionId, GateId } from './events.ts';

/** Seam 1. Production injects `{ now: () => new Date() }`. */
export interface ClockPort {
  now(): Date;
}

/** The real-time clock. Production's ClockPort. */
export const systemClock: ClockPort = { now: () => new Date() };

/**
 * Seam 2. Library-mode drain: run the mission until quiescent — every live
 * attempt finished, all remaining work blocked on a future timer or an open
 * gate. Production wraps this same function in a poller; the sandbox calls it
 * directly. Implied structural rule: the runtime stays embeddable — no daemon
 * assumption, no global singletons, deps injected.
 */
export type Drain = (missionId: MissionId, deps: DrainDeps) => Promise<DrainReport>;

export interface DrainDeps {
  clock: ClockPort;
}

export interface DrainReport {
  /** Non-null once the mission has reached a terminal state. */
  terminal: 'landed' | 'cancelled' | 'failed' | null;
  openGates: OpenGate[];
  /** Earliest pending durable timer, or null if none are pending. */
  nextTimerAt: Date | null;
  stepsExecuted: number;
}

export interface OpenGate {
  gateId: GateId;
  kind: GateKind;
  deadlineAt: Date | null;
  /** Free-form payload the gate is asking a human to approve. */
  payload: unknown;
  /** Tag of the step that raised it, when the runtime knows it. */
  stepTag?: string;
}

/** Seam 4. Programmatic mission start — no console required. */
export interface StartMissionInput {
  missionType: string;
  /** Seam 3: resolved by the governed gateway to per-tool bindings. */
  environmentId: string;
  goal: string;
  params?: Record<string, unknown>;
  overrides?: {
    modelId?: string;
    promptVersions?: Record<string, string>;
    policyRef?: string;
    budget?: BudgetEnvelope;
  };
}

export interface BudgetEnvelope {
  usd: number;
  tokens?: number;
  /** Mission deadline in DOMAIN time. */
  simDeadline?: Date;
}

/** Seam 5. Attribution is persisted verbatim in the log. */
export interface ResolveGateInput {
  gateId: GateId;
  resolution: GateResolutionWire;
  resolvedBy: string;
  reason?: string;
}

export type GateResolutionWire =
  | { kind: 'approve' }
  | { kind: 'reject'; reason: string }
  | { kind: 'provide_input'; payload: unknown }
  | { kind: 'raise_budget'; addUsd: number }
  | { kind: 'edit_then_approve'; patch: unknown }
  | { kind: 'expire' };

/**
 * The full runtime surface the harness drives. `FixtureRuntime` (pre-skeleton)
 * and the real runtime client both implement this, which is what lets the whole
 * harness be built and tested before agent-runtime exists.
 */
export interface RuntimeClient {
  startMission(input: StartMissionInput, deps: DrainDeps): Promise<MissionId>;
  drain: Drain;
  resolveGate(input: ResolveGateInput, deps: DrainDeps): Promise<void>;
}
