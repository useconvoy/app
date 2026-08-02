/**
 * Replay-mode grading — the Tier-0 path: re-run graders over a recorded
 * events JSONL + exported world bundle, zero runtime, bit-stable. Grading is
 * a pure function of (eventLog, worldBundle, scenario) — the grader cannot
 * tell a fixture from a live run.
 */

import { readFileSync } from 'node:fs';
import type { GateScriptReport, GradeRecord, WorldBundle } from '../sandbox/api.ts';
import { EventSchema, type ConvoyEvent } from '../runtime/events.ts';
import type { AnswerKey, Scenario } from '../schema/scenario.ts';
import type { Verdict } from '../schema/verdict.ts';
import { gradeTrial, type GradeTrialOpts } from './index.ts';
import { makeBundleQuery } from './world-query.ts';
import { orderEvents } from './util.ts';

export interface GradeReplayOpts extends GradeTrialOpts {
  /** Path to the recorded events JSONL file. */
  eventsJsonl: string;
  /** Parsed world bundle exported at teardown. */
  worldBundle: WorldBundle;
  scenario: Scenario;
  answerKey: AnswerKey;
  /** Absent for runs recorded without gate scripts → empty report. */
  gateReport?: GateScriptReport;
}

export function emptyGateReport(): GateScriptReport {
  return { neverRaised: [], unexpected: [], resolutions: [] };
}

export function readEventsJsonl(path: string): ConvoyEvent[] {
  const text = readFileSync(path, 'utf8');
  const events: ConvoyEvent[] = [];
  for (const line of text.split('\n')) {
    if (!line.trim()) continue;
    events.push(EventSchema.parse(JSON.parse(line)));
  }
  return orderEvents(events);
}

export async function gradeReplay(opts: GradeReplayOpts): Promise<Verdict[]> {
  const record: GradeRecord = {
    scenario: opts.scenario,
    answerKey: opts.answerKey,
    events: readEventsJsonl(opts.eventsJsonl),
    world: opts.worldBundle,
    worldQuery: makeBundleQuery(opts.worldBundle),
    gateReport: opts.gateReport ?? emptyGateReport(),
  };
  const trialOpts: GradeTrialOpts = {};
  if (opts.judgeMode !== undefined) trialOpts.judgeMode = opts.judgeMode;
  if (opts.cacheDir !== undefined) trialOpts.cacheDir = opts.cacheDir;
  if (opts.calibrationPath !== undefined) trialOpts.calibrationPath = opts.calibrationPath;
  return gradeTrial(record, trialOpts);
}
