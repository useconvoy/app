/**
 * Cost rollup math for the Logs surface, kept pure so it unit-tests
 * without a server. Inputs are the generated client's RunView shapes (type
 * imports only; nothing domain-shaped is hand-written): per-run spend
 * comes from the budget view, per-step costs from steps[].cost_usd, and
 * model groupings from steps[].model_used. Model names are operator
 * detail (law 3): the panel shows them behind a details affordance, never
 * in the main table.
 */
import type { RunView } from "@/lib/api/client";

/** Parse the API's decimal money strings; absent or bad values are 0. */
function usd(value: string | null | undefined): number {
  if (value === null || value === undefined || value === "") return 0;
  const amount = Number(value);
  return Number.isFinite(amount) ? amount : 0;
}

/** Round to cents so sums of decimal strings do not accumulate drift. */
function cents(amount: number): number {
  return Math.round(amount * 100) / 100;
}

export interface StepCost {
  stepId: string;
  description: string;
  costUsd: number;
  /** Model that ran the step, when the runtime recorded one. */
  model: string | null;
}

export interface RunSpend {
  runId: string;
  goal: string;
  spentUsd: number;
  capUsd: number | null;
  steps: StepCost[];
}

/** Per-run spend, highest spend first; steps carry their own costs. */
export function runSpends(runs: RunView[]): RunSpend[] {
  return runs
    .map((run) => ({
      runId: run.run_id,
      goal: run.goal,
      spentUsd: cents(usd(run.budget?.spent_usd)),
      capUsd: run.budget?.cap_usd ? cents(usd(run.budget.cap_usd)) : null,
      steps: run.steps
        .filter((step) => usd(step.cost_usd) > 0)
        .map((step) => ({
          stepId: step.step_id,
          description: step.description,
          costUsd: cents(usd(step.cost_usd)),
          model: step.model_used ?? null,
        })),
    }))
    .sort((a, b) => b.spentUsd - a.spentUsd);
}

export interface ModelSpend {
  model: string;
  costUsd: number;
  stepCount: number;
}

/** Step costs grouped by model, for runs where model_used exists. */
export function modelSpends(runs: RunView[]): ModelSpend[] {
  const byModel = new Map<string, ModelSpend>();
  for (const run of runs) {
    for (const step of run.steps) {
      const cost = usd(step.cost_usd);
      if (!step.model_used || cost <= 0) continue;
      const entry = byModel.get(step.model_used) ?? {
        model: step.model_used,
        costUsd: 0,
        stepCount: 0,
      };
      entry.costUsd = cents(entry.costUsd + cost);
      entry.stepCount += 1;
      byModel.set(step.model_used, entry);
    }
  }
  return [...byModel.values()].sort((a, b) => b.costUsd - a.costUsd);
}

/** Organization total across the given runs, from their budget views. */
export function totalSpend(runs: RunView[]): number {
  return cents(runs.reduce((sum, run) => sum + usd(run.budget?.spent_usd), 0));
}
