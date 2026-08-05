/**
 * Run controls: the steer composer's optimistic "Guidance sent",
 * the approve panel's 409 flip to its stale state (no retry), the
 * at_virtual toggle appearing on rehearsal runs only, and the advance
 * clock quick buttons.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RunControls, type RunControlsProps } from "@/app/(portal)/app/runs/[runId]/run-controls";
import { expectNoAxeViolations } from "../support/axe";

const refresh = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh, push: vi.fn() }),
}));

function props(over: Partial<RunControlsProps> = {}): RunControlsProps {
  return {
    runId: "run-1",
    status: "running",
    rehearsal: false,
    virtualNow: null,
    blockedSteps: [],
    planVersion: null,
    planSteps: [],
    events: [],
    commands: {},
    ...over,
  };
}

beforeEach(() => {
  refresh.mockClear();
});

describe("steer composer", () => {
  it("renders Guidance sent optimistically, before the wire answers", async () => {
    const user = userEvent.setup();
    // A promise that never settles inside the test: the optimistic state
    // must not wait for it.
    const steerRun = vi.fn().mockReturnValue(new Promise(() => undefined));
    render(<RunControls {...props({ commands: { steerRun } })} />);

    expect(screen.getByText("A redirect may propose a plan revision.")).toBeInTheDocument();
    await user.type(screen.getByRole("textbox", { name: "Guidance for this run" }), "Focus on Q3");
    await user.click(screen.getByRole("button", { name: "Send guidance" }));

    expect(screen.getByText("Guidance sent")).toBeInTheDocument();
    expect(steerRun).toHaveBeenCalledWith("run-1", "note", "Focus on Q3");
  });

  it("sends redirect mode when chosen", async () => {
    const user = userEvent.setup();
    const steerRun = vi.fn().mockResolvedValue({ kind: "accepted", steerId: "steer-1" });
    render(<RunControls {...props({ commands: { steerRun } })} />);
    await user.click(screen.getByRole("radio", { name: "Redirect" }));
    await user.type(screen.getByRole("textbox", { name: "Guidance for this run" }), "Change course");
    await user.click(screen.getByRole("button", { name: "Send guidance" }));
    expect(steerRun).toHaveBeenCalledWith("run-1", "redirect", "Change course");
  });
});

describe("plan approval", () => {
  const approvalProps = () =>
    props({
      status: "awaiting_approval",
      planVersion: 2,
      planSteps: ["Pull the current list", "Compare against the HR system"],
    });

  it("submits exactly the rendered version", async () => {
    const user = userEvent.setup();
    const approvePlan = vi.fn().mockResolvedValue({ kind: "accepted" });
    render(<RunControls {...approvalProps()} commands={{ approvePlan }} />);
    await user.click(screen.getByRole("button", { name: "Approve plan" }));
    expect(approvePlan).toHaveBeenCalledWith("run-1", 2, true, undefined);
  });

  it("flips to the stale-version state on 409 and never retries", async () => {
    const user = userEvent.setup();
    const approvePlan = vi.fn().mockResolvedValue({
      kind: "conflict",
      detail: "plan version 2 does not match the current plan version 3",
    });
    render(<RunControls {...approvalProps()} commands={{ approvePlan }} />);
    await user.click(screen.getByRole("button", { name: "Approve plan" }));

    await waitFor(() =>
      expect(screen.getByText("This plan has a newer version")).toBeInTheDocument(),
    );
    // The approve affordance is gone and the page refetches for v(n+1).
    expect(screen.queryByRole("button", { name: "Approve plan" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Review v3" })).toBeInTheDocument();
    expect(approvePlan).toHaveBeenCalledTimes(1);
    expect(refresh).toHaveBeenCalled();
  });
});

describe("gate respond", () => {
  const step = { stepId: "step-2", prompt: "Look over the exception memos before they go out" };

  it("keeps the at_virtual toggle off production runs", () => {
    render(<RunControls {...props({ blockedSteps: [step] })} />);
    expect(screen.getByText(step.prompt)).toBeInTheDocument();
    expect(screen.queryByText("Answer at a moment in rehearsal time")).not.toBeInTheDocument();
  });

  it("offers a scheduled answer on rehearsal runs and sends at_virtual", async () => {
    const user = userEvent.setup();
    const respondToGate = vi.fn().mockResolvedValue({ kind: "accepted" });
    render(
      <RunControls
        {...props({
          rehearsal: true,
          virtualNow: "2026-08-05T06:00:00Z",
          blockedSteps: [step],
          commands: { respondToGate },
        })}
      />,
    );
    await user.type(screen.getByLabelText("Your answer"), "Everyone confirmed");
    await user.click(screen.getByRole("checkbox", { name: "Answer at a moment in rehearsal time" }));
    const moment = screen.getByLabelText(/Rehearsal moment/);
    expect((moment as HTMLInputElement).value).not.toBe("");
    await user.click(screen.getByRole("button", { name: "Respond" }));

    expect(respondToGate).toHaveBeenCalledTimes(1);
    const [runId, stepId, response, atVirtual] = respondToGate.mock.calls[0]!;
    expect(runId).toBe("run-1");
    expect(stepId).toBe("step-2");
    expect(response).toBe("Everyone confirmed");
    // The scheduled moment goes over the wire timezone-aware.
    expect(atVirtual).toMatch(/Z$/);
  });

  it("anchors each respond form for notification deep links", () => {
    const { container } = render(<RunControls {...props({ blockedSteps: [step] })} />);
    expect(container.querySelector("#respond-step-2")).not.toBeNull();
    expect(container.querySelector("#resume")).not.toBeNull();
  });
});

describe("advance clock", () => {
  it("appears only on virtual-clock rehearsals and jumps with the quick buttons", async () => {
    const user = userEvent.setup();
    const advanceClock = vi.fn().mockResolvedValue({ kind: "accepted" });
    const { rerender } = render(<RunControls {...props({ commands: { advanceClock } })} />);
    expect(screen.queryByText("Advance clock")).not.toBeInTheDocument();

    rerender(
      <RunControls
        {...props({
          rehearsal: true,
          virtualNow: "2026-08-05T06:00:00.000Z",
          commands: { advanceClock },
        })}
      />,
    );
    await user.click(screen.getByRole("button", { name: "+1 day" }));
    expect(advanceClock).toHaveBeenCalledWith("run-1", "2026-08-06T06:00:00.000Z");
    await user.click(screen.getByRole("button", { name: "+1 week" }));
    expect(advanceClock).toHaveBeenCalledWith("run-1", "2026-08-12T06:00:00.000Z");
  });

  it("surfaces the 409 detail as a plain sentence", async () => {
    const user = userEvent.setup();
    const advanceClock = vi.fn().mockResolvedValue({
      kind: "conflict",
      detail: "time never moves backward",
    });
    render(
      <RunControls
        {...props({
          rehearsal: true,
          virtualNow: "2026-08-05T06:00:00.000Z",
          commands: { advanceClock },
        })}
      />,
    );
    await user.click(screen.getByRole("button", { name: "+1 day" }));
    await waitFor(() => expect(screen.getByText("time never moves backward")).toBeInTheDocument());
  });
});

describe("pause, resume, land", () => {
  it("shows Resume on a paused run and Pause otherwise, with Land alongside", async () => {
    const user = userEvent.setup();
    const resumeRun = vi.fn().mockResolvedValue({ kind: "accepted" });
    const landRun = vi.fn().mockResolvedValue({ kind: "accepted" });
    const { rerender } = render(
      <RunControls {...props({ status: "paused", commands: { resumeRun, landRun } })} />,
    );
    await user.click(screen.getByRole("button", { name: "Resume" }));
    expect(resumeRun).toHaveBeenCalledWith("run-1");

    const pauseRun = vi.fn().mockResolvedValue({ kind: "accepted" });
    rerender(<RunControls {...props({ commands: { pauseRun, landRun } })} />);
    await user.click(screen.getByRole("button", { name: "Pause" }));
    expect(pauseRun).toHaveBeenCalledWith("run-1");
    await user.click(screen.getByRole("button", { name: "Land" }));
    expect(landRun).toHaveBeenCalledWith("run-1");
  });

  it("renders nothing after the run reaches a terminal state", () => {
    const { container } = render(<RunControls {...props({ status: "completed" })} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("passes axe", async () => {
    const { container } = render(
      <RunControls
        {...props({
          status: "awaiting_approval",
          planVersion: 1,
          planSteps: ["Step one"],
          rehearsal: true,
          virtualNow: "2026-08-05T06:00:00Z",
          blockedSteps: [{ stepId: "step-2", prompt: "Check the memos" }],
        })}
      />,
    );
    await expectNoAxeViolations(container);
  });
});
