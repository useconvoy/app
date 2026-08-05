/**
 * Status-derivation helpers for the runs read path. Event-shaped inputs
 * come from the recorded control-plane fixtures, never invented.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  childRuns,
  filterGroup,
  heldKind,
  isRehearsal,
  latestBudget,
  latestLandReport,
  latestPlan,
  latestRunStatus,
  planBoardSteps,
  progress,
  routeStateFor,
  stepStatuses,
  type RunStreamEvent,
} from "@/lib/runs/status";

function fixture(name: string): RunStreamEvent[] {
  return JSON.parse(
    readFileSync(join(process.cwd(), "tests/fixtures/sse", `${name}.json`), "utf8"),
  ) as RunStreamEvent[];
}

describe("heldKind", () => {
  it("maps exactly the three stuck states", () => {
    expect(heldKind("paused")).toBe("paused");
    expect(heldKind("awaiting_approval")).toBe("awaiting_approval");
    expect(heldKind("blocked_on_human")).toBe("blocked_on_human");
    expect(heldKind("running")).toBeNull();
    expect(heldKind("completed")).toBeNull();
    expect(heldKind("failed")).toBeNull();
  });
});

describe("filterGroup", () => {
  it("groups statuses under the four list filters", () => {
    expect(filterGroup("paused")).toBe("held");
    expect(filterGroup("awaiting_approval")).toBe("held");
    expect(filterGroup("blocked_on_human")).toBe("held");
    expect(filterGroup("planning")).toBe("running");
    expect(filterGroup("running")).toBe("running");
    expect(filterGroup("landing")).toBe("running");
    expect(filterGroup("failed")).toBe("failed");
    expect(filterGroup("budget_exhausted")).toBe("failed");
    expect(filterGroup("completed")).toBe("landed");
    expect(filterGroup("landed")).toBe("landed");
    expect(filterGroup("unknown_future_status")).toBeNull();
  });
});

describe("progress", () => {
  it("counts done steps over total", () => {
    expect(progress([])).toEqual({ done: 0, total: 0 });
    expect(
      progress([{ status: "done" }, { status: "running" }, { status: "pending" }]),
    ).toEqual({ done: 1, total: 3 });
  });
});

describe("event reductions over recorded fixtures", () => {
  it("derives the latest run status from the stream", () => {
    const events = fixture("run-landed");
    expect(latestRunStatus(events, "planning")).toBe("completed");
    expect(latestRunStatus(events.slice(0, 1), "planning")).toBe("planning");
    expect(latestRunStatus([], "paused")).toBe("paused");
  });

  it("holds at blocked_on_human while a gate is open", () => {
    const events = fixture("run-gated");
    const untilGate = events.filter((event) => event.seq <= 7);
    expect(untilGate.at(-1)?.type).toBe("gate_opened");
    expect(latestRunStatus(untilGate, "planning")).toBe("blocked_on_human");
  });

  it("tracks pause and resume", () => {
    const events = fixture("run-paused-resumed");
    const untilPause = events.filter((event) => event.seq <= 6);
    expect(latestRunStatus(untilPause, "planning")).toBe("paused");
    expect(latestRunStatus(events, "planning")).toBe("completed");
  });

  it("carries the latest budget, including reservations", () => {
    const events = fixture("run-fanout");
    const midFanout = events.filter((event) => event.seq <= 9);
    expect(latestBudget(midFanout, null)).toEqual({
      cap_usd: "40",
      spent_usd: "0.0001",
      reserved_usd: "10",
    });
    expect(latestBudget([], { cap_usd: "75", spent_usd: "0", reserved_usd: "0" })).toEqual({
      cap_usd: "75",
      spent_usd: "0",
      reserved_usd: "0",
    });
  });

  it("finds the land report on run_completed", () => {
    const events = fixture("run-landed");
    const report = latestLandReport(events, null);
    expect(report).not.toBeNull();
    expect(Array.isArray(report?.["deliverables"])).toBe(true);
    expect(latestLandReport(events.slice(0, 5), null)).toBeNull();
  });

  it("flags rehearsal from the stream's sandbox flag", () => {
    expect(isRehearsal(fixture("run-rehearsal-virtual"))).toBe(true);
    expect(isRehearsal([], false)).toBe(false);
    expect(isRehearsal([], true)).toBe(true);
  });

  it("derives per-step statuses from the narrative", () => {
    const events = fixture("run-gated");
    const atGate = events.filter((event) => event.seq <= 7);
    expect(stepStatuses(atGate).get("step-1")).toBe("done");
    expect(stepStatuses(atGate).get("step-2")).toBe("blocked_on_human");
    expect(stepStatuses(events).get("step-2")).toBe("done");
  });
});

describe("planBoardSteps", () => {
  it("overlays live statuses on the plan dict and marks gated steps", () => {
    const events = fixture("run-gated");
    const atGate = events.filter((event) => event.seq <= 7);
    const board = planBoardSteps(latestPlan(atGate, null), [], stepStatuses(atGate));
    expect(board).toHaveLength(2);
    expect(board[0]).toMatchObject({ id: "step-1", status: "done", checkpoint: false });
    expect(board[1]).toMatchObject({ id: "step-2", status: "blocked_on_human", checkpoint: true });
    expect(board[0]?.sentence).toContain("Investigate");
  });

  it("falls back to the fetched view's flat steps before a plan arrives", () => {
    const board = planBoardSteps(
      null,
      [
        { step_id: "step-1", status: "done", description: "Pull the current list", attempt: 1 },
        { step_id: "step-2", status: "pending", description: "Compare against the HR system", attempt: 0 },
      ],
      new Map(),
    );
    expect(board[0]).toMatchObject({ id: "step-1", status: "done" });
    expect(board[1]).toMatchObject({ id: "step-2", status: "pending", checkpoint: false });
  });
});

describe("routeStateFor", () => {
  it("maps step statuses onto the route motif", () => {
    expect(routeStateFor("done")).toBe("done");
    expect(routeStateFor("running")).toBe("active");
    expect(routeStateFor("blocked_on_human")).toBe("held");
    expect(routeStateFor("failed")).toBe("failed");
    expect(routeStateFor("pending")).toBe("queued");
    expect(routeStateFor("ready")).toBe("queued");
  });
});

describe("childRuns", () => {
  it("compacts spawn/land pairs into one line per child (C7)", () => {
    const events = fixture("run-fanout");
    const children = childRuns(events);
    expect(children).toHaveLength(2);
    expect(children[0]).toMatchObject({
      groupId: "fanout-1",
      status: "done",
      costUsd: "0.0001",
    });
    expect(children[0]?.childRunId).toMatch(/^run-/);
    expect(children[0]?.headline).toContain("Fan-out part 1");
  });

  it("keeps a spawned child as running until it lands", () => {
    const events = fixture("run-fanout").filter((event) => event.seq <= 9);
    const children = childRuns(events);
    expect(children).toHaveLength(2);
    expect(children.every((child) => child.status === "running")).toBe(true);
    expect(children[0]?.headline).toContain("Fan-out part 1");
  });
});
