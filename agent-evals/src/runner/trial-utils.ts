/**
 * trial-utils.ts — event/trial helpers shared by the suite runner and replay.
 * No sibling-component imports: everything here depends only on the co-signed
 * schema + runtime files.
 */

import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { EventSchema, type ConvoyEvent } from '../runtime/events.ts';
import type { GateScriptReport } from '../sandbox/api.ts';
import type { Scenario } from '../schema/scenario.ts';
import type { TrialResult, Verdict } from '../schema/verdict.ts';

const DAY_MS = 86_400_000;

/** Canonical grading order: (ts, seq) — matches EventLog.forMission. */
export function sortEvents(events: ConvoyEvent[]): ConvoyEvent[] {
  return [...events].sort((a, b) => (a.ts === b.ts ? a.seq - b.seq : a.ts < b.ts ? -1 : 1));
}

export function toJsonl(events: ConvoyEvent[]): string {
  return events.map((e) => JSON.stringify(e)).join('\n') + (events.length ? '\n' : '');
}

export function readJsonlEvents(path: string): ConvoyEvent[] {
  const text = readFileSync(path, 'utf8');
  const events: ConvoyEvent[] = [];
  for (const line of text.split('\n')) {
    if (!line.trim()) continue;
    events.push(EventSchema.parse(JSON.parse(line)));
  }
  return events;
}

/** Cumulative USD spent = sum of budget_debit events. */
export function sumBudgetDebits(events: ConvoyEvent[]): number {
  let usd = 0;
  for (const e of events) if (e.type === 'budget_debit') usd += e.usd;
  return usd;
}

/** Simulated days elapsed between the first and last event. */
export function simDaysOf(events: ConvoyEvent[]): number {
  if (events.length < 2) return 0;
  const first = events[0];
  const last = events[events.length - 1];
  if (!first || !last) return 0;
  const span = Date.parse(last.ts) - Date.parse(first.ts);
  return Number.isFinite(span) && span > 0 ? span / DAY_MS : 0;
}

/** Replay grades without a live gate-script engine — empty report by contract. */
export function emptyGateReport(): GateScriptReport {
  return { neverRaised: [], unexpected: [], resolutions: [] };
}

/**
 * A trial that threw inside the harness: status harness_error plus one
 * error-status invariant verdict so the failure can never read as green.
 */
export function harnessErrorTrial(scenario: Scenario, trialIdx: number, err: unknown, wallMs: number): TrialResult {
  const message = err instanceof Error ? (err.stack ?? err.message) : String(err);
  const runId = `${scenario.id}-t${trialIdx}-${randomUUID().slice(0, 8)}`;
  const verdict: Verdict = {
    graderId: 'harness',
    graderVersion: 'harness-error-v1',
    class: 'invariant',
    scope: { runId },
    status: 'error',
    score: 0,
    evidence: [{ kind: 'note', text: `trial ${trialIdx} of ${scenario.id} threw: ${message}` }],
  };
  return {
    trialIdx,
    runId,
    status: 'harness_error',
    verdicts: [verdict],
    items: [],
    decay: null,
    costUsd: 0,
    simDays: 0,
    wallMs,
  };
}

export function timestampSlug(now: Date = new Date()): string {
  return now.toISOString().replace(/:/g, '-').replace(/\.\d+Z$/, 'Z');
}

export function defaultOutDir(now: Date = new Date()): string {
  return join('results', timestampSlug(now));
}

export function eventsFileName(scenarioId: string, trialIdx: number): string {
  return `events-${scenarioId}-t${trialIdx}.jsonl`;
}

export function worldFileName(scenarioId: string, trialIdx: number): string {
  return `world-${scenarioId}-t${trialIdx}.json`;
}

export function verdictsFileName(scenarioId: string, trialIdx: number): string {
  return `verdicts-${scenarioId}-t${trialIdx}.json`;
}
