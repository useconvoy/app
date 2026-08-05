/**
 * gradeTrial — run EVERY grader over one GradeRecord and return the flat
 * Verdict list. No short-circuiting: all graders always run; a grader that
 * throws yields a status 'error' verdict (harness failure, never subject
 * failure) and the rest continue.
 *
 * Also emits the two synthetic gate verdicts from the GateScriptReport:
 *   gate:never-raised:<stepId>  — a non-optional scripted step never matched
 *   gate:unexpected             — gates hit onUnexpectedGate: 'fail_scenario'
 * Both are invariant-class: one violation in any trial is red.
 */

import { pathToFileURL } from 'node:url';
import { readFileSync } from 'node:fs';
import type { GradeRecord } from '../sandbox/api.ts';
import type { GraderSpec, ItemSpec, Scenario } from '../schema/scenario.ts';
import type { Verdict } from '../schema/verdict.ts';
import { loadCalibrationStore, type CalibrationStore } from './calibration.ts';
import { runEndStateGrader } from './end-state.ts';
import { runJudge } from './judge.ts';
import { runProbeGrader } from './probes.ts';
import { runTrajectoryGrader } from './trajectory.ts';
import {
  canonicalJson,
  clamp01,
  evNote,
  graderVersionOf,
  sha256Hex,
  type GraderOutcome,
  type ItemCtx,
} from './util.ts';

export interface GradeTrialOpts {
  judgeMode?: 'cache-only' | 'live';
  cacheDir?: string;
  calibrationPath?: string;
}

type TsGraderSpec = Extract<GraderSpec, { grader: 'ts' }>;

/** Hash-pinned escape hatch: default export (record, itemCtx) → GraderOutcome-ish. */
async function runTsGrader(
  spec: TsGraderSpec,
  record: GradeRecord,
  itemCtx: ItemCtx | null
): Promise<GraderOutcome> {
  const source = readFileSync(spec.module, 'utf8');
  const actualHash = sha256Hex(source);
  if (actualHash !== spec.moduleHash) {
    throw new Error(
      `ts grader module hash mismatch: ${spec.module} hashes to ${actualHash}, spec pins ${spec.moduleHash}`
    );
  }
  const mod = (await import(pathToFileURL(spec.module).href)) as {
    default?: (record: GradeRecord, itemCtx: ItemCtx | null) => GraderOutcome | Promise<GraderOutcome>;
  };
  if (typeof mod.default !== 'function') {
    throw new Error(`ts grader module ${spec.module} has no default export function`);
  }
  const out = await mod.default(record, itemCtx);
  if (!out || (out.status !== 'pass' && out.status !== 'fail' && out.status !== 'error')) {
    throw new Error(`ts grader module ${spec.module} returned an invalid outcome`);
  }
  return { ...out, score: clamp01(out.score), evidence: out.evidence ?? [] };
}

function itemCtxOf(item: ItemSpec): ItemCtx {
  return { itemId: item.itemId, keyRef: item.keyRef };
}

function itemGraders(scenario: Scenario, item: ItemSpec): GraderSpec[] {
  if (item.graders === 'inherit') return scenario.itemGraderTemplate ?? [];
  return item.graders;
}

function syntheticVersion(descriptor: unknown): string {
  return sha256Hex(canonicalJson(descriptor));
}

export async function gradeTrial(record: GradeRecord, opts: GradeTrialOpts = {}): Promise<Verdict[]> {
  const judgeMode = opts.judgeMode ?? 'cache-only';
  const calibration: CalibrationStore = loadCalibrationStore(opts.calibrationPath);
  const runId = record.events[0]?.missionId ?? record.scenario.id;
  const verdicts: Verdict[] = [];
  const probeIdsReferenced = new Set<string>();

  const runOne = async (spec: GraderSpec, itemCtx: ItemCtx | null): Promise<void> => {
    const graderVersion = graderVersionOf(spec);
    const scope = itemCtx ? { runId, itemId: itemCtx.itemId } : { runId };
    let outcome: GraderOutcome;
    try {
      switch (spec.grader) {
        case 'end_state':
          outcome = runEndStateGrader(spec, record, itemCtx);
          break;
        case 'trajectory':
          outcome = runTrajectoryGrader(spec, record, itemCtx);
          break;
        case 'judge':
          outcome = await runJudge(spec, record, itemCtx, {
            mode: judgeMode,
            cacheDir: opts.cacheDir,
            calibration,
          });
          break;
        case 'probe': {
          probeIdsReferenced.add(spec.probeId);
          const probe = record.scenario.probes?.find((p) => p.probeId === spec.probeId);
          if (!probe) throw new Error(`probe "${spec.probeId}" not defined in scenario.probes`);
          outcome = runProbeGrader(probe, record);
          break;
        }
        case 'ts':
          outcome = await runTsGrader(spec, record, itemCtx);
          break;
      }
    } catch (err) {
      outcome = {
        status: 'error',
        score: 0,
        evidence: [evNote(`grader error: ${err instanceof Error ? err.message : String(err)}`)],
      };
    }
    const verdict: Verdict = {
      graderId: spec.id,
      graderVersion,
      class: spec.class,
      scope,
      status: outcome.status,
      score: clamp01(outcome.score),
      evidence: outcome.evidence,
    };
    if (outcome.lowConfidence !== undefined) verdict.lowConfidence = outcome.lowConfidence;
    if (outcome.advisory !== undefined) verdict.advisory = outcome.advisory;
    if (outcome.costUsd !== undefined) verdict.costUsd = outcome.costUsd;
    verdicts.push(verdict);
  };

  const scenario = record.scenario;
  const items = scenario.items ?? [];

  // Scenario-level graders. A scenario grader declaring scope 'item' fans out
  // over every item; everything else runs once at run scope.
  for (const spec of scenario.graders) {
    if (spec.scope === 'item' && items.length > 0) {
      for (const item of items) await runOne(spec, itemCtxOf(item));
    } else {
      await runOne(spec, null);
    }
  }

  // Per-item graders ('inherit' → scenario.itemGraderTemplate).
  for (const item of items) {
    for (const spec of itemGraders(scenario, item)) {
      await runOne(spec, itemCtxOf(item));
    }
  }

  // Probe graders not already wired through an explicit probe GraderSpec.
  for (const probe of scenario.probes ?? []) {
    if (probeIdsReferenced.has(probe.probeId)) continue;
    const spec: GraderSpec = {
      id: `probe:${probe.probeId}`,
      scope: 'run',
      weight: 1,
      class: probe.kind === 'injection_resistance' ? 'invariant' : 'quality',
      grader: 'probe',
      probeId: probe.probeId,
    };
    await runOne(spec, null);
  }

  // Synthetic gate verdicts from the gate-script report.
  for (const stepId of record.gateReport.neverRaised) {
    verdicts.push({
      graderId: `gate:never-raised:${stepId}`,
      graderVersion: syntheticVersion({ synthetic: 'gate:never-raised', stepId }),
      class: 'invariant',
      scope: { runId },
      status: 'fail',
      score: 0,
      evidence: [evNote(`scripted gate step "${stepId}" was expected but never raised`)],
    });
  }
  const approvals = scenario.approvals;
  if (
    record.gateReport.unexpected.length > 0 &&
    approvals.mode === 'scripted' &&
    approvals.onUnexpectedGate === 'fail_scenario'
  ) {
    verdicts.push({
      graderId: 'gate:unexpected',
      graderVersion: syntheticVersion({ synthetic: 'gate:unexpected' }),
      class: 'invariant',
      scope: { runId },
      status: 'fail',
      score: 0,
      evidence: record.gateReport.unexpected.map((u) =>
        evNote(`unexpected gate ${u.gateId} (${u.kind}) hit onUnexpectedGate: fail_scenario`)
      ),
    });
  }

  return verdicts;
}
