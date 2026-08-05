import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RouteStepList, type RouteStep } from "@/components/RouteStepList";
import { routines } from "@/lib/fixtures/world";

const accessReview = routines[0]!;

const steps: RouteStep[] = [
  { id: "s1", sentence: accessReview.planSteps[0]!, state: "done" },
  { id: "s2", sentence: accessReview.planSteps[1]!, state: "active" },
  { id: "s3", sentence: accessReview.planSteps[2]!, state: "held" },
  { id: "s4", sentence: accessReview.planSteps[3]!, state: "queued" },
];

describe("RouteStepList", () => {
  it("renders each step as a plain sentence in a list", () => {
    render(<RouteStepList steps={steps} />);
    const list = screen.getByRole("list");
    expect(list).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(4);
    for (const step of steps) {
      expect(screen.getByText(step.sentence)).toBeInTheDocument();
    }
  });

  it("labels every node state, never color alone", () => {
    render(<RouteStepList steps={steps} />);
    expect(screen.getByText("Done")).toBeInTheDocument();
    expect(screen.getByText("In progress")).toBeInTheDocument();
    expect(screen.getByText("Held")).toBeInTheDocument();
    expect(screen.getByText("Queued")).toBeInTheDocument();
  });

  it("pulses only the active node", () => {
    const { container } = render(<RouteStepList steps={steps} />);
    expect(container.querySelectorAll(".pulse-live")).toHaveLength(1);
  });
});
