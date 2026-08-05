import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CostRollupsPanel } from "@/components/CostRollupsPanel";
import { LogEventTable } from "@/components/LogEventTable";
import type { LogEvent } from "@/lib/logs/events";
import type { ModelSpend, RunSpend } from "@/lib/logs/rollups";
import { expectNoAxeViolations } from "../support/axe";

import gatedFixture from "../fixtures/sse/run-gated.json";

const events = (gatedFixture as unknown as LogEvent[]).slice(0, 6);
const runGoals = { [events[0]!.run_id]: "Write exception memos and hold them for review" };

describe("LogEventTable", () => {
  it("renders recorded events through the lexicon's plain labels", () => {
    render(<LogEventTable events={events} runGoals={runGoals} />);
    expect(screen.getByText("Run started")).toBeInTheDocument();
    expect(screen.getByText("Plan drafted")).toBeInTheDocument();
    expect(screen.getAllByText("Step started").length).toBeGreaterThan(0);
    // Raw event type names never render.
    expect(screen.queryByText("run_started")).not.toBeInTheDocument();
    expect(screen.queryByText("plan_created")).not.toBeInTheDocument();
  });

  it("shows the actor and links each row to the run by its goal", () => {
    render(<LogEventTable events={events} runGoals={runGoals} />);
    expect(screen.getAllByText("fixtures@convoy.test").length).toBeGreaterThan(0);
    const links = screen.getAllByRole("link", {
      name: "Write exception memos and hold them for review",
    });
    expect(links[0]!.getAttribute("href")).toBe(`/app/runs/${events[0]!.run_id}`);
  });

  it("keeps the payload behind an expandable details row", () => {
    render(<LogEventTable events={events} runGoals={runGoals} />);
    const summaries = screen.getAllByText("Details");
    expect(summaries).toHaveLength(events.length);
    // The payload text is present but collapsed inside <details>.
    const details = summaries[0]!.closest("details")!;
    expect(details.open).toBe(false);
  });

  it("labels rehearsal events (the recorded run is a rehearsal)", () => {
    render(<LogEventTable events={events} runGoals={runGoals} />);
    expect(screen.getAllByText("rehearsal")).toHaveLength(events.length);
  });

  it("renders an empty state without events", () => {
    render(<LogEventTable events={[]} runGoals={{}} />);
    expect(screen.getByText("No events yet")).toBeInTheDocument();
  });

  it("has no axe violations", async () => {
    const { container } = render(<LogEventTable events={events} runGoals={runGoals} />);
    await expectNoAxeViolations(container);
  });
});

const runRollups: RunSpend[] = [
  {
    runId: "run-b",
    goal: "Vendor due-diligence document refresh",
    spentUsd: 33.1,
    capUsd: 40,
    steps: [
      {
        stepId: "step-1",
        description: "Request updated documents",
        costUsd: 20.6,
        model: "model-large",
      },
    ],
  },
  {
    runId: "run-a",
    goal: "Quarterly user access review",
    spentUsd: 12.4,
    capUsd: 75,
    steps: [],
  },
];

const models: ModelSpend[] = [{ model: "model-large", costUsd: 20.6, stepCount: 1 }];

describe("CostRollupsPanel", () => {
  it("shows the org total and per-run spend in mono", () => {
    render(<CostRollupsPanel runs={runRollups} models={models} totalUsd={45.5} />);
    expect(screen.getByText("$45.50")).toBeInTheDocument();
    expect(screen.getByText("Vendor due-diligence document refresh")).toBeInTheDocument();
    expect(screen.getByText(/\$33\.10/)).toBeInTheDocument();
  });

  it("keeps model names behind the operator details affordance", () => {
    render(<CostRollupsPanel runs={runRollups} models={models} totalUsd={45.5} />);
    const modelRows = screen.getAllByText(/model-large/);
    for (const row of modelRows) {
      const details = row.closest("details");
      expect(details).not.toBeNull();
      expect(details!.open).toBe(false);
    }
  });

  it("has no axe violations", async () => {
    const { container } = render(
      <CostRollupsPanel runs={runRollups} models={models} totalUsd={45.5} />,
    );
    await expectNoAxeViolations(container);
  });
});
