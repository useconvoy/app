/**
 * replay.ts — the free deterministic PR-CI tier.
 *
 * Re-grades previously recorded runs: for each scenario in the eval set, load
 * the events-<scenario>-t<i>.jsonl + world-<scenario>-t<i>.json artifacts a
 * prior runSuite wrote, rebuild the GradeRecord (gateReport: empty default —
 * there is no live gate-script engine in replay), and grade + score through
 * exactly the same pipeline. Subject label is 'replay'. Judges default to
 * cache-only: replay must stay free and deterministic.
 *
 * Scenarios in the set with no recorded trials in recordsDir are skipped (a
 * filtered original run only records what it ran).
 */

import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import type { GradeRecord, WorldBundle } from '../sandbox/api.ts';
import type { AnswerKey, EvalSetConfig, Scenario } from '../schema/scenario.ts';
import type { ScenarioVerdict, SuiteResult, TrialResult, Verdict } from '../schema/verdict.ts';
import {
  loadAnswerKey,
  loadEvalSet,
  loadQuarantine,
  loadScenario,
  resolveAnswerKeyPath,
} from './store.ts';
import {
  emptyGateReport,
  eventsFileName,
  harnessErrorTrial,
  readJsonlEvents,
  simDaysOf,
  sortEvents,
  sumBudgetDebits,
  worldFileName,
} from './trial-utils.ts';
import type { ConvoyEvent } from '../runtime/events.ts';

// Sibling components (see RUNNER.NOTES.md for assumed call shapes):
import { gradeTrial } from '../graders/index.ts';
import { bundleQuery } from './world-query-shim.ts';
import { buildScenarioVerdict, buildSuiteResult, buildTrialResult } from '../scoring/index.ts';

export interface ReplaySuiteOpts {
  evalSetPath: string;
  recordsDir: string;
  /** Default 'cache-only' — replay is the free deterministic tier. */
  judgeMode?: 'cache-only' | 'live';
}

interface ReplayEntry {
  scenario: Scenario;
  answerKey: AnswerKey;
  trialIdxs: number[];
}

export async function replaySuite(opts: ReplaySuiteOpts): Promise<SuiteResult> {
  const startedAt = new Date().toISOString();
  const loaded = loadEvalSet(opts.evalSetPath);
  const config: EvalSetConfig = loaded.config;
  const quarantined = new Set(loadQuarantine(join(loaded.scenariosDir, 'quarantine.yaml')).quarantined);
  const judgeMode = opts.judgeMode ?? 'cache-only';

  if (!existsSync(opts.recordsDir)) {
    throw new Error(`records directory not found: ${opts.recordsDir}`);
  }

  // Index recorded trials by scenario id.
  const trialsByScenario = new Map<string, number[]>();
  for (const f of readdirSync(opts.recordsDir)) {
    const m = /^events-(.+)-t(\d+)\.jsonl$/.exec(f);
    if (!m || m[1] === undefined || m[2] === undefined) continue;
    const list = trialsByScenario.get(m[1]) ?? [];
    list.push(Number(m[2]));
    trialsByScenario.set(m[1], list);
  }

  const entries: ReplayEntry[] = [];
  for (const id of config.scenarios) {
    const trialIdxs = trialsByScenario.get(id);
    if (!trialIdxs || trialIdxs.length === 0) continue; // not recorded — skip
    const path = loaded.scenarioPaths.get(id);
    if (!path) {
      console.warn(`[replay] recorded scenario "${id}" has no scenario file under ${loaded.scenariosDir} — skipping`);
      continue;
    }
    const scenario = loadScenario(path);
    const keyPath = resolveAnswerKeyPath(scenario.answerKeyRef.path, path, loaded.scenariosDir);
    const answerKey = loadAnswerKey(keyPath, scenario.answerKeyRef.hash);
    entries.push({ scenario, answerKey, trialIdxs: [...trialIdxs].sort((a, b) => a - b) });
  }
  // Same admission order as runSuite so scenario ordering is comparable.
  entries.sort((a, b) => a.scenario.budgets.usd - b.scenario.budgets.usd);

  const scenarios: ScenarioVerdict[] = [];
  for (const entry of entries) {
    const trials: TrialResult[] = [];
    for (const trialIdx of entry.trialIdxs) {
      trials.push(await replayTrial(entry.scenario, entry.answerKey, opts.recordsDir, trialIdx, judgeMode));
    }
    const verdict: ScenarioVerdict = buildScenarioVerdict(
      entry.scenario,
      trials,
      config,
      quarantined.has(entry.scenario.id) ? { quarantined: true } : undefined
    );
    scenarios.push(verdict);
  }

  return buildSuiteResult({
    config,
    subject: { kind: 'scripted', label: 'replay' },
    startedAt,
    finishedAt: new Date().toISOString(),
    scenarios,
  });
}

async function replayTrial(
  scenario: Scenario,
  answerKey: AnswerKey,
  recordsDir: string,
  trialIdx: number,
  judgeMode: 'cache-only' | 'live'
): Promise<TrialResult> {
  const wallStart = Date.now();
  try {
    const events = sortEvents(readJsonlEvents(join(recordsDir, eventsFileName(scenario.id, trialIdx))));
    const worldPath = join(recordsDir, worldFileName(scenario.id, trialIdx));
    const world = JSON.parse(readFileSync(worldPath, 'utf8')) as WorldBundle;
    const record: GradeRecord = {
      scenario,
      answerKey,
      events,
      world,
      worldQuery: bundleQuery(world),
      gateReport: emptyGateReport(),
    };
    const verdicts: Verdict[] = await gradeTrial(record, { judgeMode });
    return buildTrialResult(
      trialIdx,
      events[0]?.missionId ?? `${scenario.id}-t${trialIdx}`,
      reportFromEvents(events),
      verdicts,
      scenario,
      sumBudgetDebits(events),
      simDaysOf(events),
      Date.now() - wallStart
    );
  } catch (err) {
    return harnessErrorTrial(scenario, trialIdx, err, Date.now() - wallStart);
  }
}

/**
 * Synthesize the status-bearing run-report subset from the log alone. A
 * budget_exhausted terminal maps to guardTripped 'usd' (scoring turns that
 * into budget_exceeded when onExhaustion is 'fail'); no terminal event means
 * the recorded run never landed → deadlock. A live guard_tripped run is not
 * reconstructible from events and replays as its event-visible status.
 */
function reportFromEvents(events: ConvoyEvent[]): {
  terminal: 'landed' | 'cancelled' | 'failed' | null;
  deadlock: boolean;
  guardTripped: 'usd' | null;
} {
  for (const e of events) {
    if (e.type === 'terminal_outcome') {
      if (e.status === 'budget_exhausted') return { terminal: null, deadlock: false, guardTripped: 'usd' };
      return { terminal: e.status, deadlock: false, guardTripped: null };
    }
  }
  return { terminal: null, deadlock: true, guardTripped: null };
}
