import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { BudgetMeter } from "@/components/BudgetMeter";
import { budgets } from "@/lib/fixtures/world";

import { expectNoAxeViolations } from "../support/axe";

describe("BudgetMeter", () => {
  it("renders spent of cap and the reserved amount as set aside", () => {
    render(<BudgetMeter budget={budgets.comfortable} />);
    expect(screen.getByText("$12.40 of $75")).toBeInTheDocument();
    expect(screen.getByTestId("budget-reserved")).toHaveTextContent("$6 set aside");
    expect(screen.getByTestId("budget-reserved-segment")).toBeInTheDocument();
  });

  it("hatches the reserved segment", () => {
    render(<BudgetMeter budget={budgets.comfortable} />);
    const segment = screen.getByTestId("budget-reserved-segment");
    expect(segment.style.backgroundImage).toContain("repeating-linear-gradient");
  });

  it("stays quiet below the warning threshold", () => {
    render(<BudgetMeter budget={budgets.comfortable} />);
    expect(screen.queryByText("Approaching budget")).not.toBeInTheDocument();
    expect(screen.queryByText("Out of budget")).not.toBeInTheDocument();
  });

  it("renders the 80 percent warning moment in hold colors", () => {
    render(<BudgetMeter budget={budgets.warning} />);
    const warning = screen.getByText("Approaching budget");
    // 3.59:1 on the field background is not enough for the one sentence that
    // says someone is about to run out of money.
    expect(warning).toHaveClass("text-hold-text");
    expect(warning).not.toHaveClass("text-hold");
    expect(screen.queryByText("Out of budget")).not.toBeInTheDocument();
  });

  it("renders breach in fail colors", () => {
    render(<BudgetMeter budget={budgets.breached} />);
    const breach = screen.getByText("Out of budget");
    expect(breach).toHaveClass("text-fail");
    expect(screen.queryByText("Approaching budget")).not.toBeInTheDocument();
  });

  it("has no axe violations", async () => {
    const { container } = render(<BudgetMeter budget={budgets.warning} />);
    await expectNoAxeViolations(container);
  });
});
