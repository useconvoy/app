/**
 * SandboxInstance — one live simulated environment per scenario run, plus the
 * DES driver (runUntil): drain the runtime to quiescence, apply the approval
 * script, jump the sim clock to the next scheduled event (timer, counterparty
 * delivery, or gate resolution), deliver, repeat. Nothing here sleeps on wall
 * time — a 45-day mission runs in milliseconds.
 */

import { randomBytes } from 'node:crypto';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { OpenGate } from '../runtime/ports.ts';
import type { DrainReport } from '../runtime/ports.ts';
import { EventLog } from '../runtime/log.ts';
import { parseDuration, type Scenario } from '../schema/scenario.ts';
import type {
  RunReport,
  RuntimeFactory,
  SandboxInstance,
  SandboxService,
  StopCondition,
  ToolEmulator,
} from './api.ts';
import { createSimClock } from './clock.ts';
import { createWorldStore } from './world.ts';
import { createToolGateway } from './gateway.ts';
import { createCounterpartyEngine } from './counterparty.ts';
import { createGateScriptEngine, type GateResolverFn } from './gates.ts';
import { emailEmulator } from './emulators/email.ts';
import { amsEmulator } from './emulators/ams.ts';
import { carrierPortalEmulator } from './emulators/carrierPortal.ts';
import { calendarEmulator } from './emulators/calendar.ts';

export function defaultEmulators(): ToolEmulator[] {
  return [...emailEmulator, ...amsEmulator, ...carrierPortalEmulator, ...calendarEmulator];
}

const HERE = dirname(fileURLToPath(import.meta.url));
const DEFAULT_PACK_ROOT = join(HERE, '..', '..', 'scenarios', 'packs');

function parseT0(t0: string): Date {
  const d = new Date(t0.includes('T') ? t0 : `${t0}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) throw new Error(`scenario.t0 is not a valid date: ${t0}`);
  return d;
}

export function createSandboxService(opts?: { packRoot?: string }): SandboxService {
  const packRoot = opts?.packRoot ?? DEFAULT_PACK_ROOT;

  return {
    async create(scenario: Scenario, runtimeFactory: RuntimeFactory, createOpts?: { packDir?: string }): Promise<SandboxInstance> {
      const t0 = parseT0(scenario.t0);
      const clock = createSimClock(t0);
      const log = new EventLog(clock);
      const world = createWorldStore(clock, scenario.seed);
      const packDir = createOpts?.packDir ?? join(packRoot, scenario.fixture.pack);
      world.seedFromPack(packDir, t0, scenario.seed);

      const gateway = createToolGateway({
        emulators: defaultEmulators(),
        bindings: scenario.bindings ?? {}, // empty ⇒ every tool defaults to its emulator
        log,
        world,
        missionBudgetUsd: scenario.budgets.usd,
      });

      // Engines attach BEFORE the trigger advance so unsolicited sends and
      // world subscriptions are anchored at the scenario epoch.
      const counterparties = createCounterpartyEngine({
        scripts: scenario.counterparties,
        world,
        clock,
        seed: scenario.seed,
        packDir,
      });
      const gates = createGateScriptEngine({ script: scenario.approvals, clock, seed: scenario.seed });

      const runtime = runtimeFactory({ gateway, log, world, clock });

      const id = `sim_${randomBytes(4).toString('hex')}`;

      // Schedule triggers advance the clock to t0+atSim BEFORE the mission starts.
      if (scenario.trigger.kind === 'schedule') {
        clock.advanceTo(new Date(t0.getTime() + parseDuration(scenario.trigger.atSim)));
      }
      const spec = scenario.trigger.missionSpec;
      const missionId = await runtime.startMission(
        { missionType: spec.missionType, environmentId: id, goal: spec.goal, params: spec.params },
        { clock }
      );

      const simDeadline = new Date(t0.getTime() + parseDuration(scenario.budgets.simTime));
      const wallLimitMs = parseDuration(scenario.budgets.wallClock);

      let usdSpent = 0;
      log.onAppend((e) => {
        if (e.type === 'budget_debit' && e.missionId === missionId) usdSpent += e.usd;
      });

      let resolveNowCalls = 0;
      const resolver: GateResolverFn = async (gateId, resolution, resolvedBy, reason) => {
        resolveNowCalls += 1;
        await runtime.resolveGate({ gateId, resolution, resolvedBy, reason }, { clock });
        // The label factory: every scripted resolution is a labeled human intervention.
        log.append({ type: 'human_intervention', missionId, kind: 'gate_resolution', reason });
      };

      function diagnose(r: DrainReport): string {
        const gatesPart =
          r.openGates.length > 0
            ? `${r.openGates.length} open gate(s) unmatched by script`
            : 'no open gates';
        return `deadlock: no pending timers, no queued counterparty deliveries, no scheduled gate resolutions; ${gatesPart} — nothing will ever wake this mission`;
      }

      /** Drive the DES loop; maxCycles caps iterations (step() passes 1). */
      async function drive(stop: StopCondition, maxCycles: number): Promise<RunReport> {
        const wallStart = Date.now();
        let stepsExecuted = 0;
        let lastOpenGates: OpenGate[] = [];

        const report = (over: Partial<RunReport>): RunReport => ({
          terminal: null,
          deadlock: false,
          guardTripped: null,
          openGates: lastOpenGates,
          simNow: clock.now(),
          wallMs: Date.now() - wallStart,
          stepsExecuted,
          ...over,
        });

        for (let cycle = 0; cycle < maxCycles; cycle++) {
          const r = await runtime.drain(missionId, { clock });
          stepsExecuted += r.stepsExecuted;
          lastOpenGates = r.openGates;

          if (r.terminal) return report({ terminal: r.terminal });
          if (stop.kind === 'gate-open' && r.openGates.length > 0) return report({});
          if (stop.kind === 'sim-time' && clock.now().getTime() >= stop.at.getTime()) return report({});

          const before = resolveNowCalls;
          await gates.applyScripts(r.openGates, resolver);
          if (resolveNowCalls > before) continue; // a gate resolved NOW → drain again without advancing

          const candidates = [r.nextTimerAt, counterparties.peek(), gates.nextResolutionAt()].filter(
            (d): d is Date => d !== null
          );
          if (candidates.length === 0) {
            return report({ deadlock: true, deadlockDiagnosis: diagnose(r) });
          }
          const next = new Date(Math.min(...candidates.map((d) => d.getTime())));

          if (stop.kind === 'sim-time' && next.getTime() > stop.at.getTime()) {
            clock.advanceTo(stop.at);
            return report({});
          }
          if (next.getTime() > simDeadline.getTime()) return report({ guardTripped: 'sim' });
          if (Date.now() - wallStart > wallLimitMs) return report({ guardTripped: 'wall' });
          if (usdSpent > scenario.budgets.usd) return report({ guardTripped: 'usd' });

          clock.advanceTo(next);
          counterparties.deliverDue(clock.now());
          await gates.resolveDue(clock.now(), resolver);
        }
        return report({});
      }

      const instance: SandboxInstance = {
        id,
        environmentId: id,
        clock,
        world,
        log,
        missionId,
        runUntil: (stop) => drive(stop, Number.MAX_SAFE_INTEGER),
        step: () => drive({ kind: 'terminal' }, 1),
        gateReport: () => gates.report(),
        destroy: () => ({ events: log.forMission(missionId), world: world.exportBundle() }),
      };
      return instance;
    },
  };
}
