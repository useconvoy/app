// @vitest-environment node
/**
 * Cost rollup math over a fixture RunView set: per-run spend from budget
 * views, per-step costs grouped by model, and the org total. The budget
 * shapes are the canonical fixture world's $75 / $40 / $30 set.
 */
import { describe, expect, it } from "vitest";

import type { RunView } from "@/lib/api/client";
import { modelSpends, runSpends, totalSpend } from "@/lib/logs/rollups";
import { budgets } from "@/lib/fixtures/world";

function runView(overrides: Partial<RunView>): RunView {
  return {
    run_id: "run-x",
    tenant_id: "tenant-fixtures",
    goal: "A goal",
    status: "completed",
    steps: [],
    ...overrides,
  } as RunView;
}

const runs: RunView[] = [
  runView({
    run_id: "run-a",
    goal: "Quarterly user access review",
    budget: budgets.comfortable, // cap 75, spent 12.40
    steps: [
      {
        step_id: "step-1",
        description: "Pull the current list of people",
        status: "done",
        attempt: 1,
        cost_usd: "4.10",
        model_used: "model-large",
      },
      {
        step_id: "step-2",
        description: "Compare against the HR system",
        status: "done",
        attempt: 1,
        cost_usd: "8.30",
        model_used: "model-small",
      },
      {
        step_id: "step-3",
        description: "Assemble the final packet",
        status: "running",
        attempt: 1,
        cost_usd: null,
        model_used: null,
      },
    ],
  }),
  runView({
    run_id: "run-b",
    goal: "Vendor due-diligence document refresh",
    budget: budgets.warning, // cap 40, spent 33.10
    steps: [
      {
        step_id: "step-1",
        description: "Request updated documents",
        status: "done",
        attempt: 1,
        cost_usd: "20.60",
        model_used: "model-large",
      },
      {
        step_id: "step-2",
        description: "File documents",
        status: "done",
        attempt: 1,
        cost_usd: "12.50",
      },
    ],
  }),
  runView({
    run_id: "run-c",
    goal: "Policy attestation chase",
    budget: budgets.breached, // cap 30, spent 30.00
  }),
  runView({ run_id: "run-d", goal: "No budget yet", budget: null }),
];

describe("runSpends", () => {
  it("rolls up per-run spend from the budget view, highest first", () => {
    const spends = runSpends(runs);
    expect(spends.map((spend) => spend.runId)).toEqual(["run-b", "run-c", "run-a", "run-d"]);
    expect(spends[0]).toMatchObject({ goal: "Vendor due-diligence document refresh", spentUsd: 33.1, capUsd: 40 });
    expect(spends[3]).toMatchObject({ spentUsd: 0, capUsd: null });
  });

  it("keeps only steps that actually cost something", () => {
    const runA = runSpends(runs).find((spend) => spend.runId === "run-a")!;
    expect(runA.steps).toHaveLength(2);
    expect(runA.steps.map((step) => step.costUsd)).toEqual([4.1, 8.3]);
    expect(runA.steps[0]!.model).toBe("model-large");
  });
});

describe("modelSpends", () => {
  it("groups step costs by model where model_used exists", () => {
    const models = modelSpends(runs);
    expect(models).toEqual([
      { model: "model-large", costUsd: 24.7, stepCount: 2 },
      { model: "model-small", costUsd: 8.3, stepCount: 1 },
    ]);
  });
});

describe("totalSpend", () => {
  it("sums run spends without floating drift", () => {
    expect(totalSpend(runs)).toBe(75.5);
  });
});
