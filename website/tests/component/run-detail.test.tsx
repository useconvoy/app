/**
 * Run detail composition fed by the recorded control-plane
 * fixtures. The SSE hook is mocked; everything below it renders for real.
 */
import { render, screen, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RunDetail } from "@/app/(portal)/app/runs/[runId]/run-detail";
import { useRunEvents, type RunEventStream } from "@/app/(portal)/app/runs/[runId]/use-run-events";
import type { RunView } from "@/lib/api/client";
import { friendlyDateTime } from "@/lib/format";
import { budgets } from "@/lib/fixtures/world";
import type { RunStreamEvent } from "@/lib/runs/status";
import { expectNoAxeViolations } from "../support/axe";

vi.mock("@/app/(portal)/app/runs/[runId]/use-run-events", () => ({
  useRunEvents: vi.fn(),
}));

function fixture(name: string): RunStreamEvent[] {
  return JSON.parse(
    readFileSync(join(process.cwd(), "tests/fixtures/sse", `${name}.json`), "utf8"),
  ) as RunStreamEvent[];
}

function stream(events: RunStreamEvent[], over: Partial<RunEventStream> = {}): void {
  const last = events[events.length - 1];
  vi.mocked(useRunEvents).mockReturnValue({
    events,
    connected: true,
    lastEventAt: last?.ts ?? null,
    done: last?.type === "run_completed" || last?.type === "run_failed",
    ...over,
  });
}

function initialView(events: RunStreamEvent[], over: Partial<RunView> = {}): RunView {
  const first = events[0];
  return {
    run_id: first?.run_id ?? "run-under-test",
    tenant_id: first?.tenant_id ?? "tenant-fixtures",
    parent_run_id: null,
    status: "planning",
    goal: "Compare the access lists and note differences",
    plan: null,
    land_report: null,
    budget: null,
    steps: [],
    ...over,
  };
}

beforeEach(() => {
  vi.mocked(useRunEvents).mockReset();
});

describe("RunDetail timeline", () => {
  it("hydrates the timeline from the stream with an actor on every row", () => {
    const events = fixture("run-landed");
    stream(events);
    render(<RunDetail initial={initialView(events)} />);
    expect(screen.getByText("Run started")).toBeInTheDocument();
    expect(screen.getAllByText("Step finished")).toHaveLength(2);
    expect(screen.getAllByText("Landed").length).toBeGreaterThan(0);
    // The human who started the run is attributed on its row.
    expect(screen.getByText("fixtures@convoy.test")).toBeInTheDocument();
  });

  it("collapses compaction to a quiet line with no expandable payload", () => {
    const events = fixture("run-landed");
    stream(events);
    render(<RunDetail initial={initialView(events)} />);
    const rows = screen.getAllByText("Notes tidied");
    expect(rows).toHaveLength(2);
    for (const row of rows) {
      expect(row.closest("li")?.querySelector("details")).toBeNull();
    }
  });

  it("compacts fan-out into one helper-runs group linking each child", () => {
    const events = fixture("run-fanout");
    stream(events);
    render(<RunDetail initial={initialView(events)} />);
    const group = screen.getByText("Helper runs").closest("li") as HTMLElement;
    const links = within(group).getAllByRole("link");
    expect(links).toHaveLength(2);
    expect(links[0]).toHaveAttribute(
      "href",
      expect.stringMatching(/^\/app\/runs\/run-.*fan-1/),
    );
    expect(within(group).getAllByText("Done")).toHaveLength(2);
    // The raw child events never render as individual timeline rows.
    expect(screen.queryByText("Helper run started")).not.toBeInTheDocument();
    expect(screen.queryByText("Helper run finished")).not.toBeInTheDocument();
  });
});

describe("RunDetail states", () => {
  it("renders running with the live status chip", () => {
    const events = fixture("run-landed").filter((event) => event.seq <= 3);
    stream(events);
    render(<RunDetail initial={initialView(events)} />);
    expect(screen.getAllByText("Running").length).toBeGreaterThan(0);
  });

  it("renders the held-for-a-question state with the checkpoint marker", () => {
    const events = fixture("run-gated").filter((event) => event.seq <= 7);
    stream(events);
    render(<RunDetail initial={initialView(events)} />);
    expect(screen.getAllByText("Held for you").length).toBeGreaterThan(0);
    expect(
      screen.getByText("This run asked a question and is holding until someone answers."),
    ).toBeInTheDocument();
    // The gated plan step carries the checkpoint marker on the board.
    expect(screen.getAllByText("checkpoint").length).toBeGreaterThan(0);
  });

  it("renders the paused state", () => {
    const events = fixture("run-paused-resumed").filter((event) => event.seq <= 6);
    stream(events);
    render(<RunDetail initial={initialView(events)} />);
    expect(screen.getAllByText("Paused").length).toBeGreaterThan(0);
    expect(
      screen.getByText("This run is paused and waiting for someone to resume it."),
    ).toBeInTheDocument();
  });

  it("renders awaiting approval as its own held kind", () => {
    stream([]);
    render(<RunDetail initial={initialView([], { status: "awaiting_approval" })} />);
    expect(screen.getAllByText("Waiting for approval").length).toBeGreaterThan(0);
    expect(
      screen.getByText("This run has a plan waiting for review before it starts work."),
    ).toBeInTheDocument();
  });

  it("renders the failed state", () => {
    stream([]);
    render(<RunDetail initial={initialView([], { status: "failed" })} />);
    expect(screen.getAllByText("Failed").length).toBeGreaterThan(0);
  });

  it("renders the budget-breached state through the meter", () => {
    stream([]);
    render(
      <RunDetail
        initial={initialView([], { status: "budget_exhausted", budget: budgets.breached })}
      />,
    );
    expect(screen.getAllByText("Out of budget").length).toBeGreaterThan(0);
  });
});

describe("RunDetail vitals", () => {
  it("renders the budget meter with the reserved segment", () => {
    stream([]);
    render(<RunDetail initial={initialView([], { budget: budgets.comfortable })} />);
    expect(screen.getByTestId("budget-reserved")).toHaveTextContent("$6 set aside");
    expect(screen.getByTestId("budget-reserved-segment")).toBeInTheDocument();
  });

  it("live-updates the budget from stream events", () => {
    const events = fixture("run-fanout").filter((event) => event.seq <= 9);
    stream(events);
    render(
      <RunDetail
        initial={initialView(events, { budget: { cap_usd: "40", spent_usd: "0", reserved_usd: "0" } })}
      />,
    );
    // Mid fan-out the recorded stream reserves $10 for the two children.
    expect(screen.getByTestId("budget-reserved")).toHaveTextContent("$10 set aside");
  });

  it("counts files from the land report", () => {
    const events = fixture("run-landed");
    stream(events);
    render(<RunDetail initial={initialView(events)} />);
    // "Files" appears in the vitals rail and again in the land report
    // section; the vitals count is the one under test here.
    const vitals = screen.getByRole("region", { name: "Vitals" });
    const files = within(vitals).getByText("Files");
    expect(files.parentElement).toHaveTextContent("2");
  });

  it("keeps the run id behind the operator affordance only", () => {
    const events = fixture("run-landed");
    stream(events);
    render(<RunDetail initial={initialView(events)} />);
    const details = screen.getByText("Details for operators").closest("details");
    expect(details).not.toBeNull();
    expect(within(details as HTMLElement).getByText(events[0]!.run_id)).toBeInTheDocument();
    expect(within(details as HTMLElement).getByRole("button", { name: "Copy run id" })).toBeInTheDocument();
  });

  it("shows the durable environment state from the run projection", () => {
    stream([]);
    render(
      <RunDetail
        initial={initialView([], {
          execution_session: {
            environment_id: "environment-1/sandbox",
            status: "hibernated",
            sandbox_id: null,
            sandbox_provider: "local",
            sandbox_template: "convoy-devbox-python",
            generation: 2,
            latest_checkpoint_id: "checkpoint-4",
            latest_snapshot_ref: { key: "runs/run-1/snapshot-4.tar" },
            updated_at: "2026-08-09T00:00:00Z",
          },
        })}
      />,
    );

    const runtime = screen.getByRole("region", { name: "Environment runtime" });
    expect(runtime).toHaveTextContent("Paused, compute released");
    expect(runtime).toHaveTextContent("2");
    expect(runtime).toHaveTextContent("checkpoint-4");
  });
});

describe("RunDetail rehearsal variant", () => {
  it("frames rehearsal streams with the banner and pencil treatment", () => {
    const events = fixture("run-rehearsal-virtual");
    stream(events);
    const { container } = render(<RunDetail initial={initialView(events)} />);
    expect(
      screen.getByText("You are looking at a rehearsal. Nothing here touches live systems."),
    ).toBeInTheDocument();
    expect(container.querySelector(".border-dashed")).not.toBeNull();
    // Production is the unlabeled default; the word never renders here.
    expect(screen.queryByText(/production/i)).not.toBeInTheDocument();
  });

  it("dual-timestamps rehearsal rows with virtual time primary", () => {
    const events = fixture("run-rehearsal-virtual");
    stream(events);
    render(<RunDetail initial={initialView(events)} />);
    const answered = events.find((event) => event.type === "gate_answered")!;
    const row = screen.getByText("Answered").closest("li") as HTMLElement;
    // A scripted answer's honest instant is the moment the simulated human
    // answered (payload.simulated_at), not the clock's position after the
    // advance that delivered it.
    const simulatedAt = answered.payload["simulated_at"] as string;
    const virtualStamp = friendlyDateTime(simulatedAt);
    const realStamp = friendlyDateTime(answered.ts);
    expect(within(row).getByText(virtualStamp)).toBeInTheDocument();
    expect(within(row).getByText(realStamp)).toBeInTheDocument();
    const html = row.innerHTML;
    expect(html.indexOf(virtualStamp)).toBeLessThan(html.indexOf(realStamp));
  });

  it("never adds a production label to non-rehearsal runs", () => {
    const events = fixture("run-landed");
    stream(events);
    render(<RunDetail initial={initialView(events)} />);
    // run-landed was recorded from the sandbox binding, so instead build
    // the assertion on a stream-less production view.
    vi.mocked(useRunEvents).mockReturnValue({ events: [], connected: true, lastEventAt: null, done: false });
    const { container } = render(
      <RunDetail initial={initialView([], { run_id: "run-prod", status: "running" })} />,
    );
    expect(within(container).queryByText(/production/i)).not.toBeInTheDocument();
    expect(within(container).queryByText(/rehearsal/i)).not.toBeInTheDocument();
  });
});

describe("RunDetail disconnect", () => {
  it("surfaces a dropped stream with the last-event time", () => {
    const events = fixture("run-landed").filter((event) => event.seq <= 4);
    stream(events, { connected: false, done: false });
    render(<RunDetail initial={initialView(events)} />);
    const banner = screen.getByRole("alert");
    expect(banner).toHaveTextContent("Connection lost. Reconnecting");
    expect(banner).toHaveTextContent(friendlyDateTime(events[events.length - 1]!.ts));
  });

  it("shows no banner once the stream has properly ended", () => {
    const events = fixture("run-landed");
    stream(events, { connected: false, done: true });
    render(<RunDetail initial={initialView(events)} />);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows no banner while connected", () => {
    const events = fixture("run-landed").filter((event) => event.seq <= 4);
    stream(events);
    render(<RunDetail initial={initialView(events)} />);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("RunDetail accessibility", () => {
  it("has no axe violations while streaming", async () => {
    const events = fixture("run-gated").filter((event) => event.seq <= 7);
    stream(events);
    const { container } = render(<RunDetail initial={initialView(events)} />);
    await expectNoAxeViolations(container);
  });
});
