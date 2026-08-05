/**
 * The Checkpoints inbox (W2): one typed card per stuck kind, the live
 * deadline countdown, keyboard triage (C11), the approve-from-inbox
 * version pin with the 409 flip, and the axe pass.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CheckpointsInbox } from "@/app/(portal)/app/checkpoints/checkpoints-inbox";
import type { CheckpointItem } from "@/lib/checkpoints/data";

import { expectNoAxeViolations } from "../support/axe";

const refresh = vi.fn();
const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh, push }),
}));

function item(over: Partial<CheckpointItem>): CheckpointItem {
  return {
    key: "key",
    kind: "paused",
    runId: "run-1",
    prompt: "This run is paused and waiting for someone to resume it.",
    runContext: "Quarterly user access review",
    heldMinutes: 26,
    deadline: null,
    onTimeout: null,
    planVersion: null,
    stepId: null,
    rehearsal: false,
    teamHeld: false,
    assignees: [],
    promotionRequestId: null,
    ...over,
  };
}

function world(): CheckpointItem[] {
  return [
    item({ key: "paused:run-1", kind: "paused", runId: "run-1" }),
    item({
      key: "approve:run-2",
      kind: "awaiting_approval",
      runId: "run-2",
      prompt: 'The plan for "Vendor due-diligence document refresh" is ready to review.',
      runContext: "Vendor due-diligence document refresh",
      planVersion: 3,
    }),
    item({
      key: "respond:run-3:step-2",
      kind: "blocked_on_human",
      runId: "run-3",
      stepId: "step-2",
      prompt: "Look over the exception memos before they go out",
      onTimeout: "pause",
      deadline: new Date(Date.now() + (3 * 60 + 12) * 60_000 + 30_000).toISOString(),
    }),
    item({
      key: "promotion:req-1",
      kind: "promotion",
      runId: "run-4",
      prompt: "Look over rehearsal results before anything goes live.",
      runContext: "Policy attestation chase",
      rehearsal: true,
      promotionRequestId: "req-1",
    }),
  ];
}

function commands() {
  return {
    resumeRun: vi.fn().mockResolvedValue({ kind: "accepted" }),
    approvePlan: vi.fn().mockResolvedValue({ kind: "accepted" }),
    respondToGate: vi.fn().mockResolvedValue({ kind: "accepted" }),
  };
}

beforeEach(() => {
  refresh.mockClear();
  push.mockClear();
});

describe("CheckpointsInbox", () => {
  it("renders one typed action per kind, never a merged verb", () => {
    render(<CheckpointsInbox items={world()} commands={commands()} />);
    expect(screen.getByRole("button", { name: "Resume" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve plan" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Respond" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Review" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /resolve/i })).not.toBeInTheDocument();
  });

  it("shows the version an approve card acts on and the timeout sentence", () => {
    render(<CheckpointsInbox items={world()} commands={commands()} />);
    expect(screen.getByText("Acts on v3")).toBeInTheDocument();
    expect(screen.getByText("If no one answers in time, the run pauses.")).toBeInTheDocument();
  });

  it("counts down a deadline under a day out", () => {
    render(<CheckpointsInbox items={world()} commands={commands()} />);
    expect(screen.getByText("due in 3h 12m")).toBeInTheDocument();
  });

  it("moves the roving focus with j and k", async () => {
    const user = userEvent.setup();
    render(<CheckpointsInbox items={world()} commands={commands()} />);
    const cards = screen.getAllByRole("listitem");
    cards[0]!.focus();
    expect(cards[0]).toHaveFocus();
    expect(cards[0]).toHaveAttribute("tabindex", "0");
    expect(cards[1]).toHaveAttribute("tabindex", "-1");

    await user.keyboard("j");
    expect(cards[1]).toHaveFocus();
    expect(cards[1]).toHaveAttribute("tabindex", "0");
    expect(cards[0]).toHaveAttribute("tabindex", "-1");

    await user.keyboard("k");
    expect(cards[0]).toHaveFocus();
  });

  it("fires A's approve-verb with the version rendered on the card", async () => {
    const user = userEvent.setup();
    const handlers = commands();
    render(<CheckpointsInbox items={world()} commands={handlers} />);
    const cards = screen.getAllByRole("listitem");
    cards[0]!.focus();
    await user.keyboard("j"); // onto the approve card
    await user.keyboard("a");
    expect(handlers.approvePlan).toHaveBeenCalledWith("run-2", 3, true);
  });

  it("opens reject with the reason focused on R, and rejection needs the reason", async () => {
    const user = userEvent.setup();
    const handlers = commands();
    render(<CheckpointsInbox items={world()} commands={handlers} />);
    const cards = screen.getAllByRole("listitem");
    cards[1]!.focus();
    await user.keyboard("r");
    const reason = screen.getByLabelText("What should change");
    expect(reason).toHaveFocus();
    expect(screen.getByRole("button", { name: "Send back" })).toBeDisabled();
    await user.type(reason, "Trim step three");
    await user.click(screen.getByRole("button", { name: "Send back" }));
    expect(handlers.approvePlan).toHaveBeenCalledWith("run-2", 3, false, "Trim step three");
  });

  it("ignores triage keys while typing in a field", async () => {
    const user = userEvent.setup();
    const handlers = commands();
    render(<CheckpointsInbox items={world()} commands={handlers} />);
    const answer = screen.getByLabelText("Your answer");
    await user.click(answer);
    await user.keyboard("jka");
    expect(answer).toHaveValue("jka");
    expect(handlers.resumeRun).not.toHaveBeenCalled();
    expect(handlers.approvePlan).not.toHaveBeenCalled();
  });

  it("flips an approve card to the newer-version state on 409, without retrying", async () => {
    const user = userEvent.setup();
    const handlers = commands();
    handlers.approvePlan.mockResolvedValue({
      kind: "conflict",
      detail: "plan version 3 does not match the current plan version 4",
    });
    render(<CheckpointsInbox items={world()} commands={handlers} />);
    await user.click(screen.getByRole("button", { name: "Approve plan" }));
    await waitFor(() =>
      expect(screen.getByText("This plan has a newer version")).toBeInTheDocument(),
    );
    const link = screen.getByRole("link", { name: "Review the newer version" });
    expect(link).toHaveAttribute("href", "/app/runs/run-2#approve-plan");
    expect(handlers.approvePlan).toHaveBeenCalledTimes(1);
  });

  it("submits an inline answer through the one Respond verb", async () => {
    const user = userEvent.setup();
    const handlers = commands();
    render(<CheckpointsInbox items={world()} commands={handlers} />);
    await user.type(screen.getByLabelText("Your answer"), "All good, send them");
    await user.click(screen.getByRole("button", { name: "Respond" }));
    expect(handlers.respondToGate).toHaveBeenCalledWith("run-3", "step-2", "All good, send them");
    await waitFor(() =>
      expect(screen.getByText("Sent. This clears once the run confirms.")).toBeInTheDocument(),
    );
  });

  it("labels a team-held item", () => {
    const items = world();
    items[0] = {
      ...items[0]!,
      teamHeld: true,
      assignees: [{ name: "Access reviewers", kind: "team" }],
    };
    render(<CheckpointsInbox items={items} commands={commands()} />);
    expect(screen.getByText(/Held for your team/)).toBeInTheDocument();
    const chips = screen.getByRole("list", { name: "Assigned to" });
    expect(within(chips).getByText("Access reviewers · team")).toBeInTheDocument();
  });

  it("shows the calm empty state", () => {
    render(<CheckpointsInbox items={[]} commands={commands()} />);
    expect(screen.getByText("All quiet. Nothing needs you right now.")).toBeInTheDocument();
  });

  it("documents the key mapping and passes axe", async () => {
    const { container } = render(<CheckpointsInbox items={world()} commands={commands()} />);
    const hint = screen.getByText("Keys: j next · k previous · A approve · E edit · R reject");
    expect(hint.id).toBe("checkpoints-keys");
    expect(container.querySelector("ul[aria-describedby='checkpoints-keys']")).not.toBeNull();
    await expectNoAxeViolations(container);
  });
});
