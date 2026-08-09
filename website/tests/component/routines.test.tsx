import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RoutinesList, type RoutineCard } from "@/app/(portal)/app/routines/RoutinesList";
import { RoutineDetail } from "@/app/(portal)/app/routines/[routineId]/RoutineDetail";
import { routines } from "@/lib/fixtures/world";

const cards: RoutineCard[] = routines.map((routine, index) => ({
  id: routine.id,
  name: routine.name,
  descriptor: routine.descriptor,
  systemCount: routine.systems.length,
  latestRunStatus: index === 0 ? "running" : null,
  latestRunRehearsal: false,
}));

describe("RoutinesList", () => {
  it("renders plain descriptors for every routine", () => {
    render(<RoutinesList routines={cards} showHowItFits />);
    for (const routine of routines) {
      expect(screen.getByText(routine.name)).toBeInTheDocument();
      expect(screen.getByText(routine.descriptor)).toBeInTheDocument();
    }
  });

  it("shows health from the latest run and a quiet chip otherwise", () => {
    render(<RoutinesList routines={cards} showHowItFits={false} />);
    expect(screen.getByText("Running")).toBeInTheDocument();
    expect(screen.getAllByText("No runs yet")).toHaveLength(2);
  });

  it("shows the how-it-fits strip only when asked", () => {
    const { rerender } = render(<RoutinesList routines={cards} showHowItFits />);
    expect(screen.getByText("How it fits together")).toBeInTheDocument();

    rerender(<RoutinesList routines={cards} showHowItFits={false} />);
    expect(screen.queryByText("How it fits together")).not.toBeInTheDocument();
  });

  it("has no new-routine button in phase 1", () => {
    render(<RoutinesList routines={cards} showHowItFits />);
    expect(screen.queryByRole("button", { name: /new routine/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/new routine/i)).not.toBeInTheDocument();
  });
});

function detailProps() {
  const routine = routines[0]!;
  return {
    routine,
    workspace: { id: "workspace-compliance", name: "Compliance workspace" },
    workspaceOptions: [
      { id: "workspace-compliance", name: "Compliance workspace", missing: [] },
      { id: "workspace-slim", name: "Slim workspace", missing: ["Messaging"] },
    ],
    staleNotice: null,
    canChangeWorkspace: true,
    changeWorkspaceAction: vi.fn(),
    systems: [
      {
        systemId: "identity_provider",
        displayName: "Identity provider",
        scope: "read" as const,
        sideEffecting: false,
      },
      {
        systemId: "messaging",
        displayName: "Messaging",
        scope: "write" as const,
        sideEffecting: true,
        standIn: { note: "Stand-in for Messaging: messages are held in the outbox instead of being sent." },
      },
    ],
    checkpoints: [
      {
        title: "Exception memos wait for approval before anyone is chased",
        behavior: "If no one answers in time, the run pauses.",
      },
    ],
    approvers: [
      { id: "assignment-1", assigneeType: "user" as const, assigneeId: "user-jdoe", name: "J. Doe" },
      { id: "assignment-2", assigneeType: "team" as const, assigneeId: "team-audit", name: "Audit" },
    ],
    triggers: {
      schedule: { description: "First Monday of each quarter at 9am" },
      manual: true,
    },
    runs: [
      { runId: "run-1", status: "landed", spentUsd: "12.40", rehearsal: false },
      { runId: "run-2", status: "failed", spentUsd: "3.10", rehearsal: true },
    ],
    assignablePeople: [{ id: "user-rroe", name: "R. Roe" }],
    assignableTeams: [{ id: "team-audit", name: "Audit" }],
    canAssignApprovers: true,
    canEditTriggers: false,
    canRunNow: true,
    runTarget: "rehearsal" as const,
    assignAction: vi.fn(),
    removeAction: vi.fn(),
    runNowAction: vi.fn(),
    scheduleAction: vi.fn(),
  };
}

describe("RoutineDetail", () => {
  it("renders every C3 section", () => {
    render(<RoutineDetail {...detailProps()} />);
    expect(screen.getByRole("heading", { name: routines[0]!.name })).toBeInTheDocument();
    for (const section of [
      "Systems it uses",
      "The plan",
      "Checkpoints and approvers",
      "Triggers",
      "Run history",
      "Test score trend",
      "Changelog",
      "Feedback",
    ]) {
      expect(screen.getByRole("heading", { name: section })).toBeInTheDocument();
    }
  });

  it("renders systems through the workspace grants with stand-in notes", () => {
    render(<RoutineDetail {...detailProps()} />);
    expect(screen.getByText("Identity provider")).toBeInTheDocument();
    expect(screen.getByText("View only")).toBeInTheDocument();
    expect(screen.getByText("Can update")).toBeInTheDocument();
    expect(screen.getByText("Stand-in for Messaging")).toBeInTheDocument();
  });

  it("renders the plan template as plain sentences", () => {
    render(<RoutineDetail {...detailProps()} />);
    for (const sentence of routines[0]!.planSteps) {
      expect(screen.getByText(sentence)).toBeInTheDocument();
    }
  });

  it("shows approver chips for people and teams with an assign picker", () => {
    render(<RoutineDetail {...detailProps()} />);
    expect(screen.getByText("J. Doe")).toBeInTheDocument();
    expect(screen.getByText("Team Audit")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Assign approver" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove J. Doe" })).toBeInTheDocument();
  });

  it("hides assignment controls without the permission", () => {
    render(<RoutineDetail {...detailProps()} canAssignApprovers={false} />);
    expect(screen.queryByRole("button", { name: "Assign approver" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove J. Doe" })).not.toBeInTheDocument();
  });

  it("describes triggers in plain sentences with the run-now default", () => {
    render(<RoutineDetail {...detailProps()} />);
    expect(screen.getByText("First Monday of each quarter at 9am")).toBeInTheDocument();
    expect(screen.getByText("On a schedule")).toBeInTheDocument();
    expect(screen.getByText("On demand")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run now" })).toBeInTheDocument();
    expect(screen.getByText(/rehearsal copy\. Nothing touches live systems\./)).toBeInTheDocument();
  });

  it("notes when run now goes live for promoters", () => {
    render(<RoutineDetail {...detailProps()} runTarget="production" />);
    expect(
      screen.getByText("Run now starts a live run in this workspace, within this routine's budget."),
    ).toBeInTheDocument();
  });

  it("lists run history with statuses and spend, linking each run", () => {
    render(<RoutineDetail {...detailProps()} />);
    expect(screen.getByText("Landed")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.getByText("$12.40 spent")).toBeInTheDocument();
    const links = screen.getAllByRole("link", { name: "View run" });
    expect(links.map((link) => link.getAttribute("href"))).toEqual([
      "/app/runs/run-1",
      "/app/runs/run-2",
    ]);
  });
});
