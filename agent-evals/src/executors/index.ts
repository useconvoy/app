/**
 * Executor registry — the names scenarios/CI use to pick which agent runs in
 * the sandbox. Golden must pass every grader; each violator must fail exactly
 * the grader class it was built to trip.
 */

import type { ScriptedExecutorFn } from '../sandbox/api.ts';
import { goldenRenewalPrep } from './golden.ts';
import {
  violatorDoomLoop,
  violatorDrift,
  violatorInjection,
  violatorNoGate,
  violatorSkipsItems,
  violatorWrongField,
} from './violators.ts';

export const executors: Record<string, ScriptedExecutorFn> = {
  golden: goldenRenewalPrep,
  'violator-no-gate': violatorNoGate,
  'violator-wrong-field': violatorWrongField,
  'violator-skips-items': violatorSkipsItems,
  'violator-drift': violatorDrift,
  'violator-doom-loop': violatorDoomLoop,
  'violator-injection': violatorInjection,
};

export { createScriptedRuntimeFactory } from './scripted-runtime.ts';
export { createBaselineRuntimeFactory } from './baseline.ts';
export { goldenRenewalPrep, runRenewalPrep, type RenewalPrepVariant } from './golden.ts';
export {
  violatorDoomLoop,
  violatorDrift,
  violatorInjection,
  violatorNoGate,
  violatorSkipsItems,
  violatorWrongField,
} from './violators.ts';
