/**
 * GateScriptEngine — the ApprovalScript interpreter: simultaneously the
 * unattended gate RESOLVER (so scenarios run without a human) and an
 * expected-gate ASSERTION set (unexpected gates and never-raised steps are
 * report material for graders).
 *
 * afterSim on a step simulates human latency: the resolution is queued on the
 * sim timeline and fed into the DES next-event computation, which exercises
 * park-and-resume for free.
 */

import type { OpenGate, GateResolutionWire } from '../runtime/ports.ts';
import { matches } from '../schema/match.ts';
import {
  ApprovalScriptSchema,
  parseDuration,
  type ApprovalScript,
  type GateMatcher,
  type GateResolution,
  type GateScriptStep,
  type SimDelay,
} from '../schema/scenario.ts';
import type { ClockPort } from '../runtime/ports.ts';
import type { GateScriptReport } from './api.ts';
import { mulberry32 } from './counterparty.ts';

export type GateResolverFn = (
  gateId: string,
  resolution: GateResolutionWire,
  resolvedBy: string,
  reason?: string
) => Promise<void>;

export interface GateScriptEngine {
  /** Resolve (or schedule resolution of) every newly-open gate per the script. */
  applyScripts(open: OpenGate[], resolveNow: GateResolverFn): Promise<void>;
  /** Earliest scheduled (sim-latency) resolution, or null. */
  nextResolutionAt(): Date | null;
  /** Fire every scheduled resolution due at `now`. */
  resolveDue(now: Date, resolveNow: GateResolverFn): Promise<void>;
  report(): GateScriptReport;
}

export interface GateScriptEngineOptions {
  script: ApprovalScript;
  clock: ClockPort;
  seed: number;
}

function toWire(r: GateResolution): GateResolutionWire {
  switch (r.kind) {
    case 'approve': return { kind: 'approve' };
    case 'reject': return { kind: 'reject', reason: r.reason };
    case 'provide_input': return { kind: 'provide_input', payload: r.payload };
    case 'raise_budget': return { kind: 'raise_budget', addUsd: r.addUsd };
    case 'edit_then_approve': return { kind: 'edit_then_approve', patch: r.patch }; // patch rides the wire
    case 'expire': return { kind: 'expire' };
  }
}

function gateMatches(matcher: GateMatcher, gate: OpenGate): boolean {
  if (matcher.kind && matcher.kind !== gate.kind) return false;
  if (matcher.stepTag && matcher.stepTag !== gate.stepTag) return false;
  if (matcher.payload && !matches(matcher.payload, gate.payload)) return false;
  return true;
}

interface StepState {
  step: GateScriptStep;
  fires: number;
}

interface ScheduledResolution {
  at: Date;
  seq: number;
  gateId: string;
  wire: GateResolutionWire;
  stepLabel: string;
  resolutionKind: GateResolution['kind'];
  reason?: string;
}

export function createGateScriptEngine(opts: GateScriptEngineOptions): GateScriptEngine {
  const script = ApprovalScriptSchema.parse(opts.script); // applies zod defaults
  const clock = opts.clock;
  const rand = mulberry32(opts.seed ^ 0x9e3779b9); // decorrelate from the counterparty stream

  const steps: StepState[] = script.mode === 'scripted' ? script.steps.map((step) => ({ step, fires: 0 })) : [];
  const orderedIdx = steps.map((_, i) => i).filter((i) => steps[i]!.step.ordered);
  let orderedPointer = 0;

  const handled = new Set<string>();
  const scheduled: ScheduledResolution[] = [];
  let scheduleSeq = 0;
  let approvedCount = 0;

  const resolutions: GateScriptReport['resolutions'] = [];
  const unexpected: GateScriptReport['unexpected'] = [];

  function delayMs(d: SimDelay): number {
    if (typeof d === 'string') return parseDuration(d);
    return parseDuration(d.min) + rand() * (parseDuration(d.max) - parseDuration(d.min));
  }

  async function resolve(
    entry: Omit<ScheduledResolution, 'at' | 'seq'>,
    afterSim: SimDelay | undefined,
    resolveNow: GateResolverFn
  ): Promise<boolean> {
    if (afterSim !== undefined) {
      scheduled.push({ ...entry, at: new Date(clock.now().getTime() + delayMs(afterSim)), seq: scheduleSeq++ });
      scheduled.sort((a, b) => a.at.getTime() - b.at.getTime() || a.seq - b.seq);
      return false;
    }
    await resolveNow(entry.gateId, entry.wire, `harness:${entry.stepLabel}`, entry.reason);
    resolutions.push({ gateId: entry.gateId, stepId: entry.stepLabel, resolution: entry.resolutionKind });
    return true;
  }

  /** Ordered steps must match in listed order (optional ones may be skipped); unordered match anytime. */
  function findScriptedStep(gate: OpenGate): StepState | undefined {
    for (let j = orderedPointer; j < orderedIdx.length; j++) {
      const state = steps[orderedIdx[j]!]!;
      if (state.fires >= state.step.maxFires) {
        if (j === orderedPointer) orderedPointer++;
        continue;
      }
      if (gateMatches(state.step.expect, gate)) {
        state.fires += 1;
        orderedPointer = state.fires >= state.step.maxFires ? j + 1 : j;
        return state;
      }
      if (!state.step.optional) break; // a required ordered step blocks everything after it
    }
    for (const state of steps) {
      if (state.step.ordered || state.fires >= state.step.maxFires) continue;
      if (gateMatches(state.step.expect, gate)) {
        state.fires += 1;
        return state;
      }
    }
    return undefined;
  }

  return {
    async applyScripts(open: OpenGate[], resolveNow: GateResolverFn): Promise<void> {
      for (const gate of open) {
        if (handled.has(gate.gateId)) continue;
        handled.add(gate.gateId);

        if (script.mode === 'auto_approve') {
          if (approvedCount >= script.maxGates) continue; // stop resolving → deadlock surfaces
          approvedCount += 1;
          await resolve(
            { gateId: gate.gateId, wire: { kind: 'approve' }, stepLabel: 'auto', resolutionKind: 'approve' },
            script.afterSim,
            resolveNow
          );
          continue;
        }

        if (script.mode === 'auto_reject') {
          await resolve(
            {
              gateId: gate.gateId,
              wire: { kind: 'reject', reason: script.reason },
              stepLabel: 'auto',
              resolutionKind: 'reject',
              reason: script.reason,
            },
            undefined,
            resolveNow
          );
          continue;
        }

        // scripted
        const state = findScriptedStep(gate);
        if (state) {
          const r = state.step.resolve;
          await resolve(
            {
              gateId: gate.gateId,
              wire: toWire(r),
              stepLabel: state.step.id,
              resolutionKind: r.kind,
              reason: r.kind === 'reject' ? r.reason : undefined,
            },
            state.step.afterSim,
            resolveNow
          );
          continue;
        }

        unexpected.push({ gateId: gate.gateId, kind: gate.kind });
        if (script.onUnexpectedGate !== 'fail_scenario') {
          const r = script.onUnexpectedGate.resolve;
          await resolve(
            {
              gateId: gate.gateId,
              wire: toWire(r),
              stepLabel: 'unexpected',
              resolutionKind: r.kind,
              reason: r.kind === 'reject' ? r.reason : 'unexpected gate',
            },
            undefined,
            resolveNow
          );
        }
        // fail_scenario: recorded, NOT resolved — the deadlock (or the grader) surfaces it.
      }
    },

    nextResolutionAt(): Date | null {
      return scheduled.length ? new Date(scheduled[0]!.at.getTime()) : null;
    },

    async resolveDue(now: Date, resolveNow: GateResolverFn): Promise<void> {
      while (scheduled.length && scheduled[0]!.at.getTime() <= now.getTime()) {
        const entry = scheduled.shift()!;
        await resolveNow(entry.gateId, entry.wire, `harness:${entry.stepLabel}`, entry.reason);
        resolutions.push({ gateId: entry.gateId, stepId: entry.stepLabel, resolution: entry.resolutionKind });
      }
    },

    report(): GateScriptReport {
      const neverRaised =
        script.mode === 'scripted'
          ? steps.filter((s) => !s.step.optional && s.fires === 0).map((s) => s.step.id)
          : [];
      return { neverRaised, unexpected: [...unexpected], resolutions: [...resolutions] };
    },
  };
}
