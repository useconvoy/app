/**
 * Scoring — the pure aggregation layers above Verdict:
 * ItemVerdict → DecayStats → TrialResult → ScenarioVerdict → SuiteResult.
 *
 * Load-bearing rules encoded here:
 *  - INVARIANT RULE: one non-advisory invariant-class fail/error in ANY trial
 *    turns the scenario red, no matter what the pass rates say.
 *  - 'error' is never green — a scenario whose only problem is error verdicts
 *    gets status 'error', not 'passed'.
 *  - advisory verdicts (uncalibrated judges) are reported, never gating.
 *  - an item the agent never touched scores q=0, status 'missing'.
 *  - decay thresholds come from EvalSetConfig — the ONE place they live.
 */

import type { EvalSetConfig, Scenario, TrialPolicy } from '../schema/scenario.ts';
import type {
  DecayStats,
  ItemVerdict,
  ScenarioVerdict,
  SuiteResult,
  TrialResult,
  Verdict,
} from '../schema/verdict.ts';

function clamp01(n: number): number {
  if (Number.isNaN(n)) return 0;
  return Math.min(1, Math.max(0, n));
}

// ---------------------------------------------------------------------------
// Item verdicts
// ---------------------------------------------------------------------------

/** graderId → weight, harvested from every place a spec can live in the scenario. */
function weightMap(scenario: Scenario): Map<string, number> {
  const m = new Map<string, number>();
  const add = (specs?: Scenario['graders']) => {
    for (const s of specs ?? []) m.set(s.id, s.weight ?? 1);
  };
  add(scenario.graders);
  add(scenario.itemGraderTemplate);
  for (const item of scenario.items ?? []) {
    if (item.graders !== 'inherit') add(item.graders);
  }
  return m;
}

/**
 * Group item-scoped verdicts by itemId; q = weighted mean of non-advisory
 * scores (skipped excluded). Items with zero verdicts are synthesized from
 * scenario.items with q=0, status 'missing' — the agent never touched them.
 */
export function buildItemVerdicts(scenario: Scenario, verdicts: Verdict[]): ItemVerdict[] {
  const weights = weightMap(scenario);
  const byItem = new Map<string, Verdict[]>();
  for (const v of verdicts) {
    if (v.scope.itemId === undefined) continue;
    const list = byItem.get(v.scope.itemId) ?? [];
    list.push(v);
    byItem.set(v.scope.itemId, list);
  }

  const items = (scenario.items ?? []).slice().sort((a, b) => a.ordinal - b.ordinal);
  return items.map((item) => {
    const vs = byItem.get(item.itemId) ?? [];
    if (vs.length === 0) {
      return { itemId: item.itemId, ordinal: item.ordinal, q: 0, status: 'missing' as const, verdicts: [] };
    }

    const nonAdvisory = vs.filter((v) => !v.advisory);
    const scored = nonAdvisory.filter((v) => v.status !== 'skipped');
    let q = 0;
    if (scored.length > 0) {
      let wSum = 0;
      let acc = 0;
      for (const v of scored) {
        const w = weights.get(v.graderId) ?? 1;
        wSum += w;
        acc += w * v.score;
      }
      q = wSum === 0 ? 0 : clamp01(acc / wSum);
    }

    let status: ItemVerdict['status'];
    if (nonAdvisory.some((v) => v.status === 'error')) status = 'error';
    else if (nonAdvisory.every((v) => v.status === 'pass' || v.status === 'skipped')) status = 'pass';
    else status = 'fail';

    return { itemId: item.itemId, ordinal: item.ordinal, q, status, verdicts: vs };
  });
}

// ---------------------------------------------------------------------------
// Decay stats
// ---------------------------------------------------------------------------

export type QPoint = Pick<ItemVerdict, 'ordinal' | 'q'>;

/** OLS slope of q vs ordinal, auc = mean q, decile means over ceil(n/10) items. */
export function decayStats(items: QPoint[]): DecayStats {
  const pts = items.slice().sort((a, b) => a.ordinal - b.ordinal);
  const n = pts.length;
  if (n === 0) {
    return { slope: 0, auc: 0, firstDecileMean: 0, lastDecileMean: 0, itemCount: 0, minQ: 0 };
  }
  const mean = (xs: number[]) => xs.reduce((s, x) => s + x, 0) / xs.length;
  const qs = pts.map((p) => p.q);
  const ords = pts.map((p) => p.ordinal);
  const mx = mean(ords);
  const my = mean(qs);
  let cov = 0;
  let varX = 0;
  for (let i = 0; i < n; i += 1) {
    cov += (ords[i]! - mx) * (qs[i]! - my);
    varX += (ords[i]! - mx) ** 2;
  }
  const slope = varX === 0 ? 0 : cov / varX;
  const decile = Math.ceil(n / 10);
  return {
    slope,
    auc: clamp01(my),
    firstDecileMean: clamp01(mean(qs.slice(0, decile))),
    lastDecileMean: clamp01(mean(qs.slice(n - decile))),
    itemCount: n,
    minQ: clamp01(Math.min(...qs)),
  };
}

// ---------------------------------------------------------------------------
// Trial result
// ---------------------------------------------------------------------------

/** The status-bearing subset of RunReport the scorer needs. */
export interface TrialRunReport {
  terminal?: 'landed' | 'cancelled' | 'failed' | null;
  deadlock?: boolean;
  deadlockDiagnosis?: string;
  guardTripped?: 'wall' | 'usd' | 'sim' | null;
}

/** Object-form input (equivalent to the positional signature). */
export interface BuildTrialResultInput {
  scenario: Scenario;
  trialIdx: number;
  runId: string;
  verdicts: Verdict[];
  costUsd: number;
  simDays: number;
  wallMs: number;
  /** Either a precomputed status or a RunReport-ish to map from. */
  status?: TrialResult['status'];
  report?: TrialRunReport;
  deadlockDiagnosis?: string;
}

export function trialStatusFromReport(
  report: TrialRunReport,
  scenario: Scenario
): TrialResult['status'] {
  if (report.deadlock) return 'deadlock';
  if (report.guardTripped === 'usd' && scenario.budgets.onExhaustion === 'fail') {
    return 'budget_exceeded';
  }
  if (report.guardTripped) return 'guard_tripped';
  return 'completed';
}

export function buildTrialResult(
  trialIdx: number,
  runId: string,
  report: TrialRunReport,
  verdicts: Verdict[],
  scenario: Scenario,
  costUsd: number,
  simDays: number,
  wallMs: number
): TrialResult;
export function buildTrialResult(input: BuildTrialResultInput): TrialResult;
export function buildTrialResult(
  a: number | BuildTrialResultInput,
  runId?: string,
  report?: TrialRunReport,
  verdicts?: Verdict[],
  scenario?: Scenario,
  costUsd?: number,
  simDays?: number,
  wallMs?: number
): TrialResult {
  const input: BuildTrialResultInput =
    typeof a === 'number'
      ? {
          scenario: scenario!,
          trialIdx: a,
          runId: runId!,
          report: report!,
          verdicts: verdicts!,
          costUsd: costUsd!,
          simDays: simDays!,
          wallMs: wallMs!,
        }
      : a;

  const status =
    input.status ?? trialStatusFromReport(input.report ?? {}, input.scenario);

  const items = buildItemVerdicts(input.scenario, input.verdicts);
  const decay = (input.scenario.items ?? []).length > 0 ? decayStats(items) : null;

  const result: TrialResult = {
    trialIdx: input.trialIdx,
    runId: input.runId,
    status,
    verdicts: input.verdicts,
    items,
    decay,
    costUsd: input.costUsd,
    simDays: input.simDays,
    wallMs: input.wallMs,
  };
  const diagnosis = input.deadlockDiagnosis ?? input.report?.deadlockDiagnosis;
  if (diagnosis !== undefined) result.deadlockDiagnosis = diagnosis;
  return result;
}

// ---------------------------------------------------------------------------
// Scenario verdict
// ---------------------------------------------------------------------------

function trialPolicyOf(scenario: Scenario, config: EvalSetConfig): TrialPolicy {
  return scenario.trials ?? config.defaultTrials[scenario.kind];
}

/** Mean q per ordinal across trials → the curve the chart plots. */
export function meanDecayAcrossTrials(scenario: Scenario, trials: TrialResult[]): DecayStats | null {
  const items = scenario.items ?? [];
  if (items.length === 0 || trials.length === 0) return null;
  const points: QPoint[] = items.map((item) => {
    const qs = trials.map((t) => t.items.find((i) => i.itemId === item.itemId)?.q ?? 0);
    return { ordinal: item.ordinal, q: qs.reduce((s, q) => s + q, 0) / qs.length };
  });
  return decayStats(points);
}

/**
 * A trial counts toward at_least k iff completed AND every non-advisory
 * quality verdict passed. `ignoreErrors` recounts treating error verdicts as
 * neutral — used to decide whether harness errors were the ONLY problem
 * (scenario status 'error') or the subject genuinely failed ('failed').
 */
function trialPassed(trial: TrialResult, ignoreErrors = false): boolean {
  if (trial.status !== 'completed') return false;
  return trial.verdicts
    .filter((v) => !v.advisory && v.class === 'quality')
    .every(
      (v) =>
        v.status === 'pass' || v.status === 'skipped' || (ignoreErrors && v.status === 'error')
    );
}

/** Object-form input (equivalent to the positional signature). */
export interface BuildScenarioVerdictInput {
  scenario: Scenario;
  trials: TrialResult[];
  config: EvalSetConfig;
  quarantined?: boolean;
}

export function buildScenarioVerdict(
  scenario: Scenario,
  trials: TrialResult[],
  config: EvalSetConfig,
  opts?: { quarantined?: boolean }
): ScenarioVerdict;
export function buildScenarioVerdict(input: BuildScenarioVerdictInput): ScenarioVerdict;
export function buildScenarioVerdict(
  a: Scenario | BuildScenarioVerdictInput,
  trialsArg?: TrialResult[],
  configArg?: EvalSetConfig,
  optsArg?: { quarantined?: boolean }
): ScenarioVerdict {
  const isObjectForm = trialsArg === undefined;
  const input = isObjectForm ? (a as BuildScenarioVerdictInput) : null;
  const scenario = input ? input.scenario : (a as Scenario);
  const trials = input ? input.trials : trialsArg!;
  const config = input ? input.config : configArg!;
  const opts = input ? { quarantined: input.quarantined } : optsArg;

  const policy = trialPolicyOf(scenario, config);
  const allVerdicts = trials.flatMap((t) => t.verdicts);
  const nonAdvisory = allVerdicts.filter((v) => !v.advisory);

  // INVARIANT RULE — one violation anywhere is red, regardless of pass rates.
  const invariantViolation = nonAdvisory.some(
    (v) => v.class === 'invariant' && (v.status === 'fail' || v.status === 'error')
  );
  const hasError = nonAdvisory.some((v) => v.status === 'error');

  const meanDecay = meanDecayAcrossTrials(scenario, trials);

  const detail: string[] = [];
  let rulePassed: boolean;
  let ruleWouldPassIgnoringErrors: boolean;
  if (policy.passRule.kind === 'at_least') {
    const k = policy.passRule.k;
    const good = trials.filter((t) => trialPassed(t)).length;
    rulePassed = good >= k;
    ruleWouldPassIgnoringErrors = trials.filter((t) => trialPassed(t, true)).length >= k;
    detail.push(`at_least: ${good}/${trials.length} passing trials (need ${k})`);
  } else {
    const th = config.thresholds;
    if (meanDecay === null) {
      rulePassed = false;
      ruleWouldPassIgnoringErrors = false;
      detail.push('aggregate: no per-item decay data (scenario has no items)');
    } else {
      const slopeOk = meanDecay.slope >= th.slopeMin;
      const aucOk = meanDecay.auc >= th.aucMin;
      const floorOk = meanDecay.minQ >= th.itemFloor;
      // Run-scoped quality graders (poll caps, budget shape, landed-eventually)
      // are pass/fail obligations on top of the decay thresholds — a gauntlet
      // with a flat curve but a failed run-scope check is not a pass.
      const runScopeFailed = nonAdvisory.filter(
        (v) => v.scope.itemId === undefined && v.class === 'quality' && v.status === 'fail'
      );
      rulePassed = slopeOk && aucOk && floorOk && runScopeFailed.length === 0;
      ruleWouldPassIgnoringErrors = rulePassed;
      detail.push(
        `aggregate: slope=${meanDecay.slope.toFixed(4)} (min ${th.slopeMin}), ` +
          `auc=${meanDecay.auc.toFixed(4)} (min ${th.aucMin}), ` +
          `minQ=${meanDecay.minQ.toFixed(4)} (floor ${th.itemFloor})`
      );
      if (runScopeFailed.length > 0) {
        detail.push(`aggregate: ${runScopeFailed.length} run-scope quality verdict(s) failed: ${runScopeFailed.map((v) => v.graderId).join(', ')}`);
      }
    }
  }

  if (invariantViolation) detail.push('invariant violation: at least one invariant-class verdict failed/errored');
  if (hasError) detail.push('error verdicts present: never green');

  let status: ScenarioVerdict['status'];
  if (opts?.quarantined) status = 'quarantined';
  else if (invariantViolation) status = 'failed';
  // Subject genuinely failed even with harness errors set aside → failed.
  else if (!ruleWouldPassIgnoringErrors) status = 'failed';
  // Errors were the only problem (harness fault, never subject fault) → error.
  else if (hasError || !rulePassed) status = 'error';
  else status = 'passed';

  return {
    scenarioId: scenario.id,
    status,
    invariantViolation,
    trials,
    meanDecay,
    passDetail: detail.join('; '),
    totalCostUsd: trials.reduce((s, t) => s + t.costUsd, 0),
  };
}

// ---------------------------------------------------------------------------
// Suite green + result
// ---------------------------------------------------------------------------

export function suiteGreen(
  scenarios: ScenarioVerdict[],
  config: EvalSetConfig,
  opts?: { baselineCostUsd?: number }
): { green: boolean; detail: string[] } {
  const detail: string[] = [];
  let green = true;

  const quarantined = scenarios.filter((s) => s.status === 'quarantined');
  const active = scenarios.filter((s) => s.status !== 'quarantined');
  if (quarantined.length > 0) {
    detail.push(
      `quarantined (advisory, excluded from green): ${quarantined.map((s) => s.scenarioId).join(', ')}`
    );
    for (const s of quarantined) {
      if (s.invariantViolation) {
        detail.push(`WARNING: quarantined scenario ${s.scenarioId} has an invariant violation`);
      }
    }
  }

  for (const s of active) {
    if (s.invariantViolation) {
      green = false;
      detail.push(`invariant violation in scenario ${s.scenarioId}`);
    }
  }

  const notPassed = active.filter((s) => s.status !== 'passed');
  for (const s of notPassed) {
    if (!s.invariantViolation) {
      green = false;
      detail.push(`scenario ${s.scenarioId} status=${s.status}`);
    }
  }

  const allVerdicts = active.flatMap((s) => s.trials.flatMap((t) => t.verdicts));
  const errorVerdicts = allVerdicts.filter((v) => !v.advisory && v.status === 'error');
  if (errorVerdicts.length > 0) {
    green = false;
    detail.push(
      `${errorVerdicts.length} error verdict(s): ${[...new Set(errorVerdicts.map((v) => v.graderId))].join(', ')}`
    );
  }

  // Advisory verdicts never gate — structurally excluded above. Uncalibrated
  // judges can therefore never have gated: any judge verdict allowed to gate
  // was emitted non-advisory, i.e. calibrated.
  const advisoryCount = allVerdicts.filter((v) => v.advisory).length;
  if (advisoryCount > 0) {
    detail.push(`${advisoryCount} advisory verdict(s) (uncalibrated judges) excluded from gating`);
  }

  const totalCost = scenarios.reduce((s, sc) => s + sc.totalCostUsd, 0);
  if (opts?.baselineCostUsd !== undefined) {
    const cap = opts.baselineCostUsd * (1 + config.costRegressionGuardPct / 100);
    if (totalCost > cap) {
      green = false;
      detail.push(
        `cost regression: $${totalCost.toFixed(2)} exceeds baseline $${opts.baselineCostUsd.toFixed(2)} +${config.costRegressionGuardPct}% (cap $${cap.toFixed(2)})`
      );
    } else {
      detail.push(`cost within guard: $${totalCost.toFixed(2)} <= cap $${cap.toFixed(2)}`);
    }
  }

  if (green) detail.push('green: no invariant violations, all scenarios passed, zero error verdicts');
  return { green, detail };
}

export interface BuildSuiteResultInput {
  config: EvalSetConfig;
  subject: SuiteResult['subject'];
  startedAt: string;
  finishedAt: string;
  scenarios: ScenarioVerdict[];
  baselineCostUsd?: number;
}

export function buildSuiteResult(input: BuildSuiteResultInput): SuiteResult {
  const greenOpts =
    input.baselineCostUsd !== undefined ? { baselineCostUsd: input.baselineCostUsd } : undefined;
  const { green, detail } = suiteGreen(input.scenarios, input.config, greenOpts);
  return {
    evalSet: { name: input.config.name, version: input.config.version },
    subject: input.subject,
    startedAt: input.startedAt,
    finishedAt: input.finishedAt,
    scenarios: input.scenarios,
    green,
    greenDetail: detail,
    totalCostUsd: input.scenarios.reduce((s, sc) => s + sc.totalCostUsd, 0),
  };
}
