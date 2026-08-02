/**
 * FROZEN — control arm; do not extend.
 *
 * Naive long-context baseline: one system prompt, one flat message list, a
 * single tool-use loop over the raw Anthropic Messages API (fetch, no SDK).
 * No planning, no memory, no gates, no chase cadence — whatever the model does
 * with the raw tools is the control measurement. Env-gated on
 * ANTHROPIC_API_KEY; no tests (never runs in CI).
 */

import type {
  DrainDeps,
  DrainReport,
  ResolveGateInput,
  RuntimeClient,
  StartMissionInput,
} from '../runtime/ports.ts';
import type { MissionId } from '../runtime/events.ts';
import type { RuntimeFactory, ToolGateway } from '../sandbox/api.ts';
import { createHash, randomUUID } from 'node:crypto';

const API_URL = 'https://api.anthropic.com/v1/messages';
const API_VERSION = '2023-06-01';
const DEFAULT_MODEL_ID = 'claude-sonnet-4-6';
/** Pricing constants for the control arm — $3/M input, $15/M output. */
const USD_PER_INPUT_TOKEN = 3 / 1_000_000;
const USD_PER_OUTPUT_TOKEN = 15 / 1_000_000;
const MAX_TURNS = 100;
const MAX_TOKENS_PER_TURN = 4096;

/** Anthropic tool names may not contain '.'; expose 'email.send' as 'email__send'. */
const TOOL_NAMES = [
  'email.send',
  'email.list_inbox',
  'ams.get_policy',
  'ams.update_policy',
  'portal.request_loss_runs',
  'portal.check_status',
  'portal.download',
  'calendar.create_event',
  'calendar.list_events',
];

const wireName = (tool: string): string => tool.replaceAll('.', '__');
const realName = (wire: string): string => wire.replaceAll('__', '.');

interface ApiContentBlock {
  type: string;
  text?: string;
  id?: string;
  name?: string;
  input?: unknown;
}

interface ApiResponse {
  content?: ApiContentBlock[];
  stop_reason?: string;
  usage?: { input_tokens?: number; output_tokens?: number };
  error?: { type?: string; message?: string };
}

interface BaselineMission {
  input: StartMissionInput;
  messages: unknown[];
  turns: number;
  terminal: DrainReport['terminal'];
}

export function createBaselineRuntimeFactory(opts: { modelId?: string }): RuntimeFactory {
  return ({ gateway, log, clock }) => {
    if (!process.env.ANTHROPIC_API_KEY) {
      throw new Error(
        'baseline executor: ANTHROPIC_API_KEY is not set. The frozen naive baseline calls the ' +
          'real Anthropic API — export ANTHROPIC_API_KEY, or run a scripted executor instead.',
      );
    }
    const apiKey = process.env.ANTHROPIC_API_KEY;
    const modelId = opts.modelId ?? DEFAULT_MODEL_ID;
    const missions = new Map<MissionId, BaselineMission>();

    const tools = TOOL_NAMES.map((tool) => ({
      name: wireName(tool),
      description: `Invoke the ${tool} tool of the mission environment. Pass arguments as a JSON object.`,
      input_schema: { type: 'object' as const },
    }));

    async function callModel(system: string, messages: unknown[]): Promise<ApiResponse> {
      const res = await fetch(API_URL, {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
          'x-api-key': apiKey,
          'anthropic-version': API_VERSION,
        },
        body: JSON.stringify({
          model: modelId,
          max_tokens: MAX_TOKENS_PER_TURN,
          system,
          tools,
          messages,
        }),
      });
      const body = (await res.json()) as ApiResponse;
      if (!res.ok) {
        throw new Error(`baseline: Anthropic API ${res.status}: ${body.error?.message ?? 'unknown error'}`);
      }
      return body;
    }

    async function runTool(
      missionId: MissionId,
      gw: ToolGateway,
      block: ApiContentBlock,
    ): Promise<{ type: 'tool_result'; tool_use_id: string; content: string; is_error?: boolean }> {
      const toolUseId = block.id ?? randomUUID();
      try {
        const result = await gw.invoke(realName(block.name ?? ''), block.input ?? {}, { missionId });
        return { type: 'tool_result', tool_use_id: toolUseId, content: JSON.stringify(result ?? null) };
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        return { type: 'tool_result', tool_use_id: toolUseId, content: message, is_error: true };
      }
    }

    function recordTerminal(missionId: MissionId, m: BaselineMission, status: 'landed' | 'failed', summary: string) {
      if (m.terminal !== null) return;
      m.terminal = status;
      log.append({ type: 'terminal_outcome', missionId, status, judgedBy: 'agent', summary });
    }

    const client: RuntimeClient = {
      async startMission(input: StartMissionInput, _deps: DrainDeps): Promise<MissionId> {
        const missionId = randomUUID();
        const system = `${input.goal}\n\ntoday is ${clock.now().toISOString().slice(0, 10)}`;
        missions.set(missionId, {
          input,
          messages: [
            {
              role: 'user',
              content: `Begin the mission. Mission params: ${JSON.stringify(input.params ?? {})}`,
            },
          ],
          turns: 0,
          terminal: null,
        });
        log.append({
          type: 'mission_started',
          missionId,
          missionType: input.missionType,
          environmentId: input.environmentId,
          goal: input.goal,
          spec: {
            modelId,
            promptHashes: { system: createHash('sha256').update(system).digest('hex') },
            ...(input.params !== undefined ? { params: input.params } : {}),
          },
        });
        return missionId;
      },

      async drain(missionId: MissionId, _deps: DrainDeps): Promise<DrainReport> {
        const m = missions.get(missionId);
        if (!m) throw new Error(`baseline drain: unknown missionId ${missionId}`);
        let steps = 0;

        while (m.terminal === null && m.turns < MAX_TURNS) {
          const system = `${m.input.goal}\n\ntoday is ${clock.now().toISOString().slice(0, 10)}`;
          let response: ApiResponse;
          try {
            response = await callModel(system, m.messages);
          } catch (err) {
            recordTerminal(missionId, m, 'failed', err instanceof Error ? err.message : String(err));
            break;
          }
          m.turns += 1;
          steps += 1;

          const tokensIn = response.usage?.input_tokens ?? 0;
          const tokensOut = response.usage?.output_tokens ?? 0;
          const costUsd = tokensIn * USD_PER_INPUT_TOKEN + tokensOut * USD_PER_OUTPUT_TOKEN;
          log.append({ type: 'model_call', missionId, modelId, tokensIn, tokensOut, costUsd });
          log.append({ type: 'budget_debit', missionId, usd: costUsd, tokens: tokensIn + tokensOut, resource: 'model' });

          const content = response.content ?? [];
          m.messages.push({ role: 'assistant', content });

          if (response.stop_reason === 'tool_use') {
            const toolUses = content.filter((b) => b.type === 'tool_use');
            const results = [];
            for (const block of toolUses) results.push(await runTool(missionId, gateway, block));
            m.messages.push({ role: 'user', content: results });
            continue;
          }

          const finalText = content
            .filter((b) => b.type === 'text')
            .map((b) => b.text ?? '')
            .join('\n')
            .slice(0, 2000);
          recordTerminal(missionId, m, 'landed', finalText || `stopped: ${response.stop_reason ?? 'unknown'}`);
        }

        if (m.terminal === null && m.turns >= MAX_TURNS) {
          recordTerminal(missionId, m, 'failed', `baseline turn cap (${MAX_TURNS}) reached`);
        }

        return { terminal: m.terminal, openGates: [], nextTimerAt: null, stepsExecuted: steps };
      },

      async resolveGate(_input: ResolveGateInput, _deps: DrainDeps): Promise<void> {
        throw new Error('baseline executor raises no gates; resolveGate is not supported');
      },
    };

    return client;
  };
}
