/**
 * suite-runner.ts — run an eval set against a subject (scripted executor or a
 * real runtime factory) and produce a SuiteResult.
 *
 * Flow per trial: sandbox create → runUntil(terminal) → gateReport → destroy →
 * write artifacts (events JSONL, world bundle, verdicts) → gradeTrial →
 * buildTrialResult. Scenario trials run sequentially; DIFFERENT scenarios run
 * under a simple promise pool (maxConcurrent, default 2). USD cap uses
 * cheapest-first admission (scenarios sorted by budgets.usd ascending) and is
 * checked cumulatively before every trial. Deadlocked / guard-tripped runs are
 * still graded — partial credit is the point. A trial that throws becomes a
 * harness_error TrialResult with an error verdict (never green).
 */

import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import type { ConvoyEvent } from '../runtime/events.ts';
import type { GradeRecord, RunReport, RuntimeFactory, WorldBundle } from '../sandbox/api.ts';
import type { AnswerKey, EvalSetConfig, Scenario } from '../schema/scenario.ts';
import type { ScenarioVerdict, SuiteResult, TrialResult, Verdict } from '../schema/verdict.ts';
import {
  loadAnswerKey,
  loadEvalSet,
  loadQuarantine,
  loadScenario,
  resolveAnswerKeyPath,
  type LoadedEvalSet,
} from './store.ts';
import {
  defaultOutDir,
  eventsFileName,
  harnessErrorTrial,
  simDaysOf,
  sortEvents,
  sumBudgetDebits,
  toJsonl,
  verdictsFileName,
  worldFileName,
} from './trial-utils.ts';

import { createSandboxService } from '../sandbox/index.ts';
import { createScriptedRuntimeFactory, executors } from '../executors/index.ts';
import { gradeTrial } from '../graders/index.ts';
import { bundleQuery } from './world-query-shim.ts';
import { buildScenarioVerdict, buildSuiteResult, buildTrialResult } from '../scoring/index.ts';

export type SuiteSubject =
  | { kind: 'scripted'; executor: string }
  | { kind: 'runtime'; factory: RuntimeFactory; label: string };

export interface RunSuiteOpts {
  evalSetPath: string;
  subject: SuiteSubject;
  scenarioFilter?: string[];
  trialsOverride?: number;
  /** Promise-pool width over scenarios (a scenario's trials stay sequential). */
  maxConcurrent?: number;
  /** Cumulative USD cap, checked before each trial; cheapest-first admission. */
  usdCap?: number;
  judgeMode?: 'cache-only' | 'live';
  /** Default results/<timestamp>. */
  outDir?: string;
}

interface ScenarioEntry {
  scenario: Scenario;
  answerKey: AnswerKey;
}

export async function runSuite(opts: RunSuiteOpts): Promise<SuiteResult> {
  const startedAt = new Date().toISOString();
  const loaded: LoadedEvalSet = loadEvalSet(opts.evalSetPath);
  const config: EvalSetConfig = loaded.config;
  const quarantined = new Set(loadQuarantine(join(loaded.scenariosDir, 'quarantine.yaml')).quarantined);

  const filter = opts.scenarioFilter;
  if (filter) {
    for (const id of filter) {
      if (!config.scenarios.includes(id)) {
        throw new Error(`filter scenario "${id}" is not in eval set ${config.name}@${config.version}`);
      }
    }
  }
  const wantedIds = config.scenarios.filter((id) => !filter || filter.includes(id));

  const entries: ScenarioEntry[] = wantedIds.map((id) => {
    const path = loaded.scenarioPaths.get(id);
    if (!path) throw new Error(`scenario "${id}" has no *.scenario.json under ${loaded.scenariosDir}`);
    const scenario = loadScenario(path);
    const keyPath = resolveAnswerKeyPath(scenario.answerKeyRef.path, path, loaded.scenariosDir);
    const answerKey = loadAnswerKey(keyPath, scenario.answerKeyRef.hash);
    return { scenario, answerKey };
  });
  // Cheapest-first admission: under a USD cap, the cheap scenarios get to run.
  entries.sort((a, b) => a.scenario.budgets.usd - b.scenario.budgets.usd);

  let factory: RuntimeFactory;
  if (opts.subject.kind === 'scripted') {
    const executorFn = executors[opts.subject.executor];
    if (executorFn === undefined) {
      throw new Error(
        `unknown scripted executor "${opts.subject.executor}" (available: ${Object.keys(executors).join(', ')})`
      );
    }
    factory = createScriptedRuntimeFactory(executorFn);
  } else {
    factory = opts.subject.factory;
  }

  const outDir = opts.outDir ?? defaultOutDir();
  mkdirSync(outDir, { recursive: true });
  const service = createSandboxService({ packRoot: join(loaded.scenariosDir, 'packs') });

  const state = { totalCostUsd: 0 };

  const runTrial = async (scenario: Scenario, answerKey: AnswerKey, trialIdx: number): Promise<TrialResult> => {
    const wallStart = Date.now();
    try {
      const instance = await service.create(scenario, factory);
      let report: RunReport;
      try {
        report = await instance.runUntil({ kind: 'terminal' });
      } catch (err) {
        try {
          instance.destroy();
        } catch {
          // teardown failure is secondary to the original error
        }
        throw err;
      }
      const gateReport = instance.gateReport();
      const teardown = instance.destroy();
      const events: ConvoyEvent[] = sortEvents(teardown.events);
      const world: WorldBundle = teardown.world;

      writeFileSync(join(outDir, eventsFileName(scenario.id, trialIdx)), toJsonl(events));
      writeFileSync(join(outDir, worldFileName(scenario.id, trialIdx)), JSON.stringify(world, null, 2) + '\n');

      const record: GradeRecord = {
        scenario,
        answerKey,
        events,
        world,
        worldQuery: bundleQuery(world),
        gateReport,
      };
      const verdicts: Verdict[] = await gradeTrial(
        record,
        opts.judgeMode !== undefined ? { judgeMode: opts.judgeMode } : undefined
      );
      writeFileSync(join(outDir, verdictsFileName(scenario.id, trialIdx)), JSON.stringify(verdicts, null, 2) + '\n');

      const trial: TrialResult = buildTrialResult(
        trialIdx,
        events[0]?.missionId ?? instance.missionId,
        report,
        verdicts,
        scenario,
        sumBudgetDebits(events),
        simDaysOf(events),
        Date.now() - wallStart
      );
      return trial;
    } catch (err) {
      return harnessErrorTrial(scenario, trialIdx, err, Date.now() - wallStart);
    }
  };

  const runScenario = async (entry: ScenarioEntry): Promise<ScenarioVerdict> => {
    const { scenario, answerKey } = entry;
    const trialsN =
      opts.trialsOverride ?? scenario.trials?.n ?? config.defaultTrials[scenario.kind].n;
    const trials: TrialResult[] = [];
    let cappedOut = false;
    for (let i = 0; i < trialsN; i++) {
      if (opts.usdCap !== undefined && state.totalCostUsd >= opts.usdCap) {
        cappedOut = true;
        break;
      }
      const trial = await runTrial(scenario, answerKey, i);
      state.totalCostUsd += trial.costUsd;
      trials.push(trial);
    }
    if (trials.length === 0 && cappedOut) {
      return {
        scenarioId: scenario.id,
        status: 'skipped_budget',
        invariantViolation: false,
        trials: [],
        meanDecay: null,
        passDetail: `skipped: cumulative cost $${state.totalCostUsd.toFixed(4)} reached the suite usd cap ($${opts.usdCap})`,
        totalCostUsd: 0,
      };
    }
    // Quarantined scenarios still run, but only advisorily — they never gate
    // (scoring excludes status 'quarantined' from suiteGreen).
    let verdict: ScenarioVerdict = buildScenarioVerdict(
      scenario,
      trials,
      config,
      quarantined.has(scenario.id) ? { quarantined: true } : undefined
    );
    if (cappedOut) {
      verdict = { ...verdict, passDetail: `${verdict.passDetail} [usd cap stopped ${trialsN - trials.length} trial(s)]` };
    }
    return verdict;
  };

  // Promise pool over scenarios; each worker owns one scenario at a time so a
  // scenario's trials are strictly sequential.
  const scenarioVerdicts: ScenarioVerdict[] = new Array(entries.length);
  let cursor = 0;
  const width = Math.max(1, Math.min(opts.maxConcurrent ?? 2, entries.length || 1));
  const worker = async (): Promise<void> => {
    for (;;) {
      const idx = cursor;
      cursor += 1;
      if (idx >= entries.length) return;
      const entry = entries[idx];
      if (!entry) return;
      scenarioVerdicts[idx] = await runScenario(entry);
    }
  };
  await Promise.all(Array.from({ length: width }, () => worker()));

  const result: SuiteResult = buildSuiteResult({
    config,
    subject: subjectDescriptor(opts.subject),
    startedAt,
    finishedAt: new Date().toISOString(),
    scenarios: scenarioVerdicts.filter((v): v is ScenarioVerdict => v !== undefined),
  });
  writeFileSync(join(outDir, 'suite-result.json'), JSON.stringify(result, null, 2) + '\n');
  return result;
}

function subjectDescriptor(subject: SuiteSubject): SuiteResult['subject'] {
  return subject.kind === 'scripted'
    ? { kind: 'scripted', label: `scripted:${subject.executor}` }
    : { kind: 'runtime', label: subject.label };
}
