import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ImprovementCard } from "@/components/ImprovementCard";
import type { Improvement } from "@/lib/api/learning";
import { expectNoAxeViolations } from "../support/axe";

const proposed: Improvement = {
  id: "improvement-1",
  routineId: "routine-access-review",
  title: "Chase non-responders before assembling memos",
  summary: "Reminders that go out earlier bring sign-offs back faster.",
  diff: [
    { kind: "change", text: "Move the chase step ahead of memo assembly" },
    { kind: "add", text: "Send a second reminder two days after the first" },
    { kind: "remove", text: "Wait for the memo batch before chasing" },
  ],
  evidence: {
    testScoreBefore: 84,
    testScoreAfter: 91,
    runIds: ["run-1", "run-2", "run-3"],
  },
  status: "proposed",
};

describe("ImprovementCard", () => {
  it("renders the diff as marked mono lines", () => {
    render(<ImprovementCard improvement={proposed} canShip={false} />);
    expect(screen.getByText("Move the chase step ahead of memo assembly")).toBeInTheDocument();
    expect(screen.getByText("Send a second reminder two days after the first")).toBeInTheDocument();
    expect(screen.getByText("Wait for the memo batch before chasing")).toBeInTheDocument();
    // Markers carry the meaning alongside color: + for add, - for remove.
    expect(screen.getByText("Added:")).toBeInTheDocument();
    expect(screen.getByText("Removed:")).toBeInTheDocument();
    expect(screen.getByText("Changed:")).toBeInTheDocument();
  });

  it("states the test-score evidence in plain words", () => {
    render(<ImprovementCard improvement={proposed} canShip={false} />);
    expect(screen.getByText("Test score 84 -> 91 across 3 rehearsal runs")).toBeInTheDocument();
  });

  it("hides approve and ship without the capability", () => {
    render(
      <ImprovementCard
        improvement={proposed}
        canShip={false}
        approveAction={vi.fn()}
        shipAction={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Ship" })).not.toBeInTheDocument();
  });

  it("offers approve on a proposed improvement with the capability", () => {
    render(
      <ImprovementCard
        improvement={proposed}
        canShip
        approveAction={vi.fn()}
        shipAction={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Ship" })).not.toBeInTheDocument();
    expect(screen.getByText("Proposed")).toBeInTheDocument();
  });

  it("offers ship once approved, and nothing once shipped", () => {
    const approved: Improvement = { ...proposed, status: "approved" };
    const { rerender } = render(
      <ImprovementCard improvement={approved} canShip approveAction={vi.fn()} shipAction={vi.fn()} />,
    );
    expect(screen.getByRole("button", { name: "Ship" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();

    const shipped: Improvement = { ...proposed, status: "shipped", shippedAt: "2026-08-04T10:00:00Z" };
    rerender(
      <ImprovementCard improvement={shipped} canShip approveAction={vi.fn()} shipAction={vi.fn()} />,
    );
    expect(screen.queryByRole("button", { name: "Ship" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.getByText("Shipped")).toBeInTheDocument();
  });

  it("has no axe violations", async () => {
    const { container } = render(
      <ImprovementCard improvement={proposed} canShip approveAction={vi.fn()} shipAction={vi.fn()} />,
    );
    await expectNoAxeViolations(container);
  });
});
