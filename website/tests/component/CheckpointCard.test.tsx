import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { CheckpointCard } from "@/components/CheckpointCard";

import { expectNoAxeViolations } from "../support/axe";

const base = {
  prompt: "Approve the exception memo for the finance group",
  runContext: "Quarterly user access review",
  heldMinutes: 26,
};

describe("CheckpointCard", () => {
  it("renders the one typed action per stuck state, never a merged button", () => {
    const { rerender } = render(<CheckpointCard {...base} kind="paused" />);
    let buttons = screen.getAllByRole("button");
    expect(buttons).toHaveLength(1);
    expect(buttons[0]).toHaveTextContent("Resume");

    rerender(<CheckpointCard {...base} kind="awaiting_approval" planVersion={3} />);
    buttons = screen.getAllByRole("button");
    expect(buttons).toHaveLength(1);
    expect(buttons[0]).toHaveTextContent("Approve plan");

    rerender(<CheckpointCard {...base} kind="blocked_on_human" />);
    buttons = screen.getAllByRole("button");
    expect(buttons).toHaveLength(1);
    expect(buttons[0]).toHaveTextContent("Respond");
  });

  it("shows the plan version an approve-plan card acts on", () => {
    render(<CheckpointCard {...base} kind="awaiting_approval" planVersion={3} />);
    expect(screen.getByText("Acts on v3")).toBeInTheDocument();
  });

  it("renders held age, deadline, run context, and timeout behavior", () => {
    render(
      <CheckpointCard
        {...base}
        kind="blocked_on_human"
        deadline="2026-08-05T17:00:00"
        onTimeout="pause"
      />,
    );
    expect(screen.getByText("HELD 26M")).toBeInTheDocument();
    expect(screen.getByText("DUE 08-05")).toBeInTheDocument();
    expect(screen.getByText("Quarterly user access review")).toBeInTheDocument();
    expect(screen.getByText("If no one answers in time, the run pauses.")).toBeInTheDocument();
  });

  it("renders keyboard hints as key caps", () => {
    render(<CheckpointCard {...base} kind="awaiting_approval" planVersion={2} />);
    expect(screen.getByText("A")).toBeInTheDocument();
    expect(screen.getByText("approve")).toBeInTheDocument();
    expect(screen.getByText("E")).toBeInTheDocument();
    expect(screen.getByText("edit")).toBeInTheDocument();
    expect(screen.getByText("R")).toBeInTheDocument();
    expect(screen.getByText("reject")).toBeInTheDocument();
  });

  it("renders person and team assignee chips", () => {
    render(
      <CheckpointCard
        {...base}
        kind="blocked_on_human"
        assignees={[
          { name: "J. Doe", kind: "person" },
          { name: "Compliance", kind: "team" },
        ]}
      />,
    );
    expect(screen.getByText("J. Doe")).toBeInTheDocument();
    expect(screen.getByText("Compliance · team")).toBeInTheDocument();
  });

  it("shows responder attribution after an answer, with no action", () => {
    render(<CheckpointCard {...base} kind="blocked_on_human" answeredBy="R. Roe" />);
    expect(screen.getByText("Answered by R. Roe")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("invokes the typed action", async () => {
    const user = userEvent.setup();
    const onAction = vi.fn();
    render(<CheckpointCard {...base} kind="paused" onAction={onAction} />);
    await user.click(screen.getByRole("button", { name: "Resume" }));
    expect(onAction).toHaveBeenCalledTimes(1);
  });

  it("marks rehearsal checkpoints with the dashed graphite treatment", () => {
    const { container } = render(<CheckpointCard {...base} kind="paused" rehearsal />);
    expect(screen.getByText("rehearsal")).toBeInTheDocument();
    expect(container.querySelector("article.border-dashed")).not.toBeNull();
  });

  it("has no axe violations", async () => {
    const { container } = render(
      <CheckpointCard
        {...base}
        kind="awaiting_approval"
        planVersion={3}
        deadline="2026-08-05T17:00:00"
        onTimeout="pause"
        assignees={[{ name: "J. Doe", kind: "person" }]}
      />,
    );
    await expectNoAxeViolations(container);
  });
});
