/**
 * ScriptedRuntime — a cooperative-coroutine RuntimeClient for scripted
 * executors (golden / violators). Implements the same RuntimeClient seam the
 * real agent-runtime will implement, so the harness drives both identically.
 *
 * Scheduling model: the executor fn runs as an ordinary async function whose
 * awaits on ctx.wait / ctx.awaitInbound / ctx.raiseGate "park" it on a
 * condition. drain() is the ONLY thing that advances the coroutine: it scans
 * parked entries, settles every condition currently satisfiable, yields the
 * microtask/immediate queue so the coroutine runs until it parks again, and
 * repeats until a scan settles nothing. No wall-clock sleeps anywhere — the
 * sandbox advances the SimClock between drains.
 */

import { createHash, randomUUID } from 'node:crypto';
import type {
  DrainDeps,
  DrainReport,
  OpenGate,
  ResolveGateInput,
  StartMissionInput,
  GateResolutionWire,
  RuntimeClient,
} from '../runtime/ports.ts';
import type { GateId, MissionId } from '../runtime/events.ts';
import type {
  ExecutorCtx,
  RuntimeFactory,
  ScriptedExecutorFn,
  WorldMessage,
} from '../sandbox/api.ts';

const RESUME_CAP_PER_DRAIN = 10_000;

interface Parked {
  id: string;
  kind: 'timer' | 'inbound' | 'gate';
  /** Epoch ms; set for timers only — feeds DrainReport.nextTimerAt. */
  fireAt?: number;
  ready(): boolean;
  settle(): void;
}

interface MissionState {
  missionId: MissionId;
  terminal: DrainReport['terminal'];
  parked: Parked[];
  consumedInbound: Set<string>;
  openGates: Map<GateId, OpenGate>;
  gateResolutions: Map<GateId, GateResolutionWire>;
}

function sha256(s: string): string {
  return createHash('sha256').update(s).digest('hex');
}

/** Let the started coroutine(s) run until they park again. */
function tick(): Promise<void> {
  return new Promise((resolve) => setImmediate(resolve));
}

export function createScriptedRuntimeFactory(executor: ScriptedExecutorFn): RuntimeFactory {
  return ({ gateway, log, world, clock }) => {
    const missions = new Map<MissionId, MissionState>();
    const gateToMission = new Map<GateId, MissionState>();
    const gateItemRefs = new Map<GateId, string>();

    function recordTerminal(
      state: MissionState,
      status: 'landed' | 'failed' | 'cancelled',
      summary: string | undefined,
    ): void {
      if (state.terminal !== null) return;
      state.terminal = status;
      log.append({
        type: 'terminal_outcome',
        missionId: state.missionId,
        status,
        judgedBy: 'agent',
        ...(summary !== undefined ? { summary } : {}),
      });
    }

    function makeCtx(state: MissionState, input: StartMissionInput): ExecutorCtx {
      const missionId = state.missionId;
      return {
        missionId,
        clock,
        gateway,
        log,
        goal: input.goal,
        params: input.params ?? {},

        wait(durationMs: number): Promise<void> {
          const timerId = randomUUID();
          const fireAt = clock.now().getTime() + durationMs;
          log.append({
            type: 'timer_scheduled',
            missionId,
            timerId,
            fireAt: new Date(fireAt).toISOString(),
          });
          return new Promise<void>((resolve) => {
            state.parked.push({
              id: timerId,
              kind: 'timer',
              fireAt,
              ready: () => clock.now().getTime() >= fireAt,
              settle: () => {
                log.append({ type: 'timer_fired', missionId, timerId });
                resolve();
              },
            });
          });
        },

        awaitInbound(match): Promise<WorldMessage> {
          const find = (): WorldMessage | undefined => {
            const candidates = world
              .listMessages({ direction: 'inbound' })
              .filter((m) => !state.consumedInbound.has(m.id))
              .filter(
                (m) =>
                  match.toContains === undefined ||
                  m.to.some((t) => t.includes(match.toContains as string)) ||
                  m.from.includes(match.toContains as string),
              )
              .filter(
                (m) => match.subjectRegex === undefined || new RegExp(match.subjectRegex).test(m.subject),
              )
              .filter(
                (m) => match.afterTs === undefined || Date.parse(m.ts) > Date.parse(match.afterTs as string),
              );
            candidates.sort((a, b) => Date.parse(a.ts) - Date.parse(b.ts));
            return candidates[0];
          };
          return new Promise<WorldMessage>((resolve) => {
            state.parked.push({
              id: randomUUID(),
              kind: 'inbound',
              ready: () => find() !== undefined,
              settle: () => {
                const m = find();
                if (!m) throw new Error('awaitInbound settled without a matching message');
                state.consumedInbound.add(m.id);
                resolve(m);
              },
            });
          });
        },

        raiseGate(kind, payload, opts): Promise<{ resolution: string; payload?: unknown }> {
          const gateId = randomUUID();
          log.append({
            type: 'gate_raised',
            missionId,
            gateId,
            kind,
            payload,
            deadlineAt: null,
            ...(opts?.stepTag !== undefined ? { stepTag: opts.stepTag } : {}),
            ...(opts?.itemRef !== undefined ? { itemRef: opts.itemRef } : {}),
          });
          const open: OpenGate = {
            gateId,
            kind,
            deadlineAt: null,
            payload,
            ...(opts?.stepTag !== undefined ? { stepTag: opts.stepTag } : {}),
          };
          state.openGates.set(gateId, open);
          gateToMission.set(gateId, state);
          if (opts?.itemRef !== undefined) gateItemRefs.set(gateId, opts.itemRef);
          return new Promise((resolve) => {
            state.parked.push({
              id: gateId,
              kind: 'gate',
              ready: () => state.gateResolutions.has(gateId),
              settle: () => {
                const r = state.gateResolutions.get(gateId);
                if (!r) throw new Error(`gate ${gateId} settled without a resolution`);
                resolve({
                  resolution: r.kind,
                  payload:
                    r.kind === 'provide_input' ? r.payload : r.kind === 'edit_then_approve' ? r.patch : undefined,
                });
              },
            });
          });
        },

        emitArtifact(a): void {
          const mime = a.mime ?? 'application/json';
          const file = world.putFile({ name: a.tag, mime, content: a.content });
          log.append({
            type: 'artifact_created',
            missionId,
            artifactId: file.id,
            hash: file.hash,
            tag: a.tag,
            mime,
            bytes: Buffer.byteLength(a.content, 'utf8'),
            ...(a.itemRef !== undefined ? { itemRef: a.itemRef } : {}),
          });
        },

        land(summary): void {
          recordTerminal(state, 'landed', summary ?? 'landed');
        },

        fail(reason): void {
          recordTerminal(state, 'failed', reason);
        },
      };
    }

    /** Settle every parked entry whose condition holds right now. */
    function scanAndSettle(state: MissionState): number {
      const ready = state.parked.filter((p) => p.ready());
      if (ready.length === 0) return 0;
      const readySet = new Set(ready);
      state.parked = state.parked.filter((p) => !readySet.has(p));
      for (const p of ready) p.settle();
      return ready.length;
    }

    function buildReport(state: MissionState, stepsExecuted: number): DrainReport {
      let nextTimerAt: Date | null = null;
      for (const p of state.parked) {
        if (p.kind !== 'timer' || p.fireAt === undefined) continue;
        if (nextTimerAt === null || p.fireAt < nextTimerAt.getTime()) nextTimerAt = new Date(p.fireAt);
      }
      return {
        terminal: state.terminal,
        openGates: [...state.openGates.values()],
        nextTimerAt,
        stepsExecuted,
      };
    }

    const client: RuntimeClient = {
      async startMission(input: StartMissionInput, _deps: DrainDeps): Promise<MissionId> {
        const missionId = randomUUID();
        const state: MissionState = {
          missionId,
          terminal: null,
          parked: [],
          consumedInbound: new Set(),
          openGates: new Map(),
          gateResolutions: new Map(),
        };
        missions.set(missionId, state);
        log.append({
          type: 'mission_started',
          missionId,
          missionType: input.missionType,
          environmentId: input.environmentId,
          goal: input.goal,
          spec: {
            modelId: 'scripted',
            promptHashes: { executor: sha256(executor.toString()) },
            ...(input.params !== undefined ? { params: input.params } : {}),
          },
        });
        const ctx = makeCtx(state, input);
        // Start the coroutine; do NOT await — drain() drives it.
        void executor(ctx).then(
          () => {
            if (state.terminal === null) recordTerminal(state, 'landed', 'executor returned');
          },
          (err: unknown) => {
            const message = err instanceof Error ? err.message : String(err);
            recordTerminal(state, 'failed', message);
          },
        );
        return missionId;
      },

      async drain(missionId: MissionId, _deps: DrainDeps): Promise<DrainReport> {
        const state = missions.get(missionId);
        if (!state) throw new Error(`drain: unknown missionId ${missionId}`);
        let resumes = 0;
        // Initial flush: let a just-started (or just-resolved) coroutine run to its park point.
        await tick();
        for (;;) {
          const settled = scanAndSettle(state);
          resumes += settled;
          if (resumes > RESUME_CAP_PER_DRAIN) {
            throw new Error(
              `drain: resume cap (${RESUME_CAP_PER_DRAIN}) exceeded for mission ${missionId} — runaway executor loop?`,
            );
          }
          if (settled === 0) break;
          await tick();
        }
        return buildReport(state, resumes);
      },

      async resolveGate(input: ResolveGateInput, _deps: DrainDeps): Promise<void> {
        const state = gateToMission.get(input.gateId);
        if (!state) throw new Error(`resolveGate: unknown gateId ${input.gateId}`);
        const r = input.resolution;
        const raisedItemRef = gateItemRefs.get(input.gateId);
        log.append({
          type: 'gate_resolved',
          missionId: state.missionId,
          gateId: input.gateId,
          resolution: r.kind,
          resolvedBy: input.resolvedBy,
          // Resolution inherits the raising gate's item attribution so
          // item-scoped trajectory graders see the full gate lifecycle.
          ...(raisedItemRef !== undefined ? { itemRef: raisedItemRef } : {}),
          ...(input.reason !== undefined ? { reason: input.reason } : {}),
          ...(r.kind === 'edit_then_approve' ? { patch: r.patch } : {}),
        });
        state.gateResolutions.set(input.gateId, r);
        state.openGates.delete(input.gateId);
        // The parked gate entry is now ready(); the next drain() settles it.
      },
    };

    return client;
  };
}
