/**
 * Cost rollups beside the Logs table: total spend, spend per run, and the
 * per-step breakdown with model names, which are operator detail (law 3)
 * and therefore live in mono behind a details affordance, never in the
 * main rows.
 */
import { money } from "@/lib/format";
import type { ModelSpend, RunSpend } from "@/lib/logs/rollups";
import { copy, logsCopy } from "@/lexicon";

export interface CostRollupsPanelProps {
  runs: RunSpend[];
  models: ModelSpend[];
  totalUsd: number;
}

export function CostRollupsPanel({ runs, models, totalUsd }: CostRollupsPanelProps) {
  return (
    <section aria-label={logsCopy.spendTitle} className="rounded-lg border border-line bg-card p-5">
      <h2 className="font-display text-lg text-ink">{logsCopy.spendTitle}</h2>
      <p className="mt-1 text-sm text-muted">{logsCopy.totalSpend}</p>
      <p className="mt-1 font-mono text-2xl text-ink">{money(totalUsd)}</p>

      <ul className="m-0 mt-4 list-none space-y-3 p-0">
        {runs.map((run) => (
          <li key={run.runId} className="border-t border-line-soft pt-3">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <span className="text-sm text-ink">{run.goal}</span>
              <span className="font-mono text-xs text-muted">
                {money(run.spentUsd)}
                {run.capUsd !== null && <> of {money(run.capUsd)}</>}
              </span>
            </div>
            {run.steps.length > 0 && (
              <details className="mt-1">
                <summary className="cursor-pointer text-xs text-muted">
                  {copy.operatorDetails}
                </summary>
                <ul className="m-0 mt-1 list-none space-y-1 p-0">
                  {run.steps.map((step) => (
                    <li key={step.stepId} className="flex flex-wrap justify-between gap-2 text-xs">
                      <span className="text-muted">{step.description}</span>
                      <span className="font-mono text-graphite">
                        {step.model && <>{step.model} · </>}
                        {money(step.costUsd)}
                      </span>
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </li>
        ))}
      </ul>

      {models.length > 0 && (
        <details className="mt-4 border-t border-line-soft pt-3">
          <summary className="cursor-pointer text-xs text-muted">{logsCopy.spendByStep}</summary>
          <ul className="m-0 mt-1 list-none space-y-1 p-0">
            {models.map((model) => (
              <li key={model.model} className="flex flex-wrap justify-between gap-2 text-xs">
                <span className="font-mono text-graphite">{model.model}</span>
                <span className="font-mono text-graphite">
                  {money(model.costUsd)} · {model.stepCount}{" "}
                  {model.stepCount === 1 ? "step" : "steps"}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
