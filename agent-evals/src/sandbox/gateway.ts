/**
 * ToolGateway — the only way any executor touches the world.
 *
 * Enforces the effect-class rule: pure tools emit one collapsed `tool_call`
 * event; effectful tools go through the two-phase envelope (intent → approved
 * → executed → result) with idempotency-key dedupe (crash-replay safety: a
 * re-invoke with the same key returns the recorded result without re-running
 * the handler, logging intent + result only). Every invoke appends a flat
 * budget_debit (v1 metering).
 */

import { createHash } from 'node:crypto';
import type { EventLog } from '../runtime/log.ts';
import type { Binding } from '../schema/scenario.ts';
import type { ToolCallCtx, ToolEmulator, ToolGateway, WorldStore } from './api.ts';
import { stableStringify } from './world.ts';

const FLAT_TOOL_DEBIT_USD = 0.001;

export interface ToolGatewayOptions {
  emulators: ToolEmulator[];
  bindings: Record<string, Binding>;
  log: EventLog;
  world: WorldStore;
  missionBudgetUsd?: number;
}

export function createToolGateway(opts: ToolGatewayOptions): ToolGateway {
  const { bindings, log, world } = opts;
  const byTool = new Map<string, ToolEmulator>();
  for (const em of opts.emulators) {
    if (byTool.has(em.tool)) throw new Error(`duplicate tool emulator registered: ${em.tool}`);
    byTool.set(em.tool, em);
  }

  /** idempotencyKey → recorded successful result (errors are NOT recorded: retries re-run). */
  const executed = new Map<string, { result: unknown }>();
  /** Deterministic per-gateway counter so auto-minted keys never collide. */
  let invokeSeq = 0;

  function base(ctx: ToolCallCtx) {
    return { missionId: ctx.missionId, itemRef: ctx.itemRef, stepId: ctx.stepId };
  }

  function debit(ctx: ToolCallCtx): void {
    log.append({ ...base(ctx), type: 'budget_debit', usd: FLAT_TOOL_DEBIT_USD, resource: 'tool' });
  }

  return {
    async invoke(tool: string, args: unknown, ctx: ToolCallCtx): Promise<unknown> {
      const emulator = byTool.get(tool);
      if (!emulator) throw new Error(`unknown tool: ${tool}`);
      const binding = bindings[tool];
      if (binding && binding.kind !== 'emulator') {
        throw new Error(`binding kind '${binding.kind}' for tool '${tool}' not implemented in v1`);
      }

      if (!emulator.effectful) {
        // Pure/read tool: one collapsed event, result (or error) inline.
        try {
          const result = await emulator.handler(args, world, ctx);
          log.append({ ...base(ctx), type: 'tool_call', tool, args, result });
          debit(ctx);
          return result;
        } catch (err) {
          log.append({ ...base(ctx), type: 'tool_call', tool, args, error: String(err instanceof Error ? err.message : err) });
          debit(ctx);
          throw err;
        }
      }

      // Effectful tool: two-phase envelope + idempotency dedupe. The key is
      // CALLER-minted (per attempt): same key ⇒ dedupe (crash replay never
      // re-fires); no key ⇒ fresh per invoke, so intentional retries re-run.
      const idempotencyKey =
        ctx.idempotencyKey ??
        createHash('sha256')
          .update(`${tool}\n${stableStringify(args)}\n${ctx.missionId}\n${invokeSeq++}`, 'utf8')
          .digest('hex');

      log.append({ ...base(ctx), type: 'tool_intent', tool, args, idempotencyKey });

      const prior = executed.get(idempotencyKey);
      if (prior) {
        // Dedupe: return the recorded result WITHOUT re-running the handler.
        log.append({ ...base(ctx), type: 'tool_result', tool, idempotencyKey, result: prior.result });
        debit(ctx);
        return prior.result;
      }

      log.append({ ...base(ctx), type: 'tool_approved', tool, idempotencyKey });
      try {
        const result = await emulator.handler(args, world, ctx);
        log.append({ ...base(ctx), type: 'tool_executed', tool, idempotencyKey, args });
        log.append({ ...base(ctx), type: 'tool_result', tool, idempotencyKey, result });
        debit(ctx);
        executed.set(idempotencyKey, { result });
        return result;
      } catch (err) {
        const message = String(err instanceof Error ? err.message : err);
        log.append({ ...base(ctx), type: 'tool_result', tool, idempotencyKey, error: message });
        debit(ctx);
        throw err;
      }
    },

    manifestHash(): string {
      const lines = [...byTool.values()]
        .map((e) => `${e.tool}:${e.effectful}`)
        .sort();
      return createHash('sha256').update(lines.join('\n'), 'utf8').digest('hex');
    },
  };
}
