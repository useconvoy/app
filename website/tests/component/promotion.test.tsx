/**
 * Land report + promotion review (C8): the report's plain-language
 * sections, the stand-in outbox rows derived from the run's own records,
 * and the promoter gating on the decision (as props; the server action
 * enforces for real).
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { PromotionDecision } from "@/app/(portal)/app/promotions/[requestId]/promotion-decision";
import { LandReportSection } from "@/components/LandReportSection";
import type { RunStreamEvent } from "@/lib/runs/status";
import { deriveStandInOutbox } from "@/lib/runs/land-report";

import { expectNoAxeViolations } from "../support/axe";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: vi.fn(), push: vi.fn() }),
}));

/** The recorded rehearsal run's real land report and budget. */
function landedFixture(): { report: Record<string, unknown>; budget: Record<string, string> } {
  const events = JSON.parse(
    readFileSync(join(process.cwd(), "tests/fixtures/sse/run-gated.json"), "utf8"),
  ) as RunStreamEvent[];
  const completed = events.find((event) => event.type === "run_completed")!;
  return {
    report: completed.payload["land_report"] as Record<string, unknown>,
    budget: completed.payload["budget"] as Record<string, string>,
  };
}

const steps = [
  {
    step_id: "step-1",
    description: "Investigate: Write exception memos and hold them for review",
    status: "done",
  },
  {
    step_id: "step-2",
    description: "Send the exception memos for review",
    status: "done",
  },
];

describe("LandReportSection", () => {
  it("renders outcome, steps, exceptions, spend, and files from the real report", () => {
    const { report, budget } = landedFixture();
    render(
      <LandReportSection
        report={report}
        budget={{ cap_usd: budget.cap_usd!, spent_usd: budget.spent_usd!, reserved_usd: budget.reserved_usd! }}
        steps={steps}
        rehearsal={false}
      />,
    );
    expect(
      screen.getByText("Finished: Write exception memos and hold them for review"),
    ).toBeInTheDocument();
    expect(screen.getByText("2 done · 0 failed · 0 skipped")).toBeInTheDocument();
    expect(screen.getByText("No exceptions")).toBeInTheDocument();
    expect(screen.getByText(/step-1\.json/)).toBeInTheDocument();
    expect(screen.getByText(/step-2\.json/)).toBeInTheDocument();
    // Spend vs cap through the budget meter (tiny spend rounds to $0).
    expect(screen.getByText("$0 of $40")).toBeInTheDocument();
    // Production reports carry no stand-in outbox.
    expect(screen.queryByText("What this routine would have done")).not.toBeInTheDocument();
  });

  it("shows the stand-in outbox on rehearsal reports, one row per recorded step", () => {
    const { report } = landedFixture();
    render(<LandReportSection report={report} budget={null} steps={steps} rehearsal />);
    expect(screen.getByText("What this routine would have done")).toBeInTheDocument();
    const table = screen.getByRole("table");
    expect(within(table).getByText("What")).toBeInTheDocument();
    expect(within(table).getByText("To whom or where")).toBeInTheDocument();
    expect(within(table).getByText("What would have gone out")).toBeInTheDocument();
    // The side-effecting step is held by the stand-in; the other stays
    // with the run's files. Both point at their actual output.
    expect(
      within(table).getByText("Send the exception memos for review"),
    ).toBeInTheDocument();
    expect(
      within(table).getByText("Outside this organization, held by the stand-in"),
    ).toBeInTheDocument();
    expect(within(table).getByText("Kept with the run's files")).toBeInTheDocument();
    expect(within(table).getByText("step-2.json")).toBeInTheDocument();
  });

  it("passes axe", async () => {
    const { report } = landedFixture();
    const { container } = render(
      <LandReportSection report={report} budget={null} steps={steps} rehearsal />,
    );
    await expectNoAxeViolations(container);
  });
});

describe("deriveStandInOutbox", () => {
  it("prefers an explicit outbox list when the runtime provides one", () => {
    const rows = deriveStandInOutbox(
      {
        outbox: [
          { what: "Send a reminder", to: "j.doe@example.com", content: "Please confirm access." },
        ],
      },
      steps,
    );
    expect(rows).toEqual([
      { what: "Send a reminder", where: "j.doe@example.com", content: "Please confirm access." },
    ]);
  });

  it("skips steps that never finished", () => {
    const { report } = landedFixture();
    const rows = deriveStandInOutbox(report, [
      { step_id: "step-1", description: "Send everything", status: "failed" },
    ]);
    expect(rows).toEqual([]);
  });
});

describe("PromotionDecision", () => {
  it("keeps the decision behind the promoter capability", () => {
    render(<PromotionDecision requestId="req-1" canPromote={false} />);
    expect(screen.getByText("Promoting needs a promoter.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Promote" })).not.toBeInTheDocument();
  });

  it("promotes with one click", async () => {
    const user = userEvent.setup();
    const promote = vi.fn().mockResolvedValue({ kind: "accepted", status: "promoted" });
    const sendBack = vi.fn();
    render(<PromotionDecision requestId="req-1" canPromote actions={{ promote, sendBack }} />);
    await user.click(screen.getByRole("button", { name: "Promote" }));
    expect(promote).toHaveBeenCalledWith("req-1");
    expect(sendBack).not.toHaveBeenCalled();
  });

  it("requires a note to send back", async () => {
    const user = userEvent.setup();
    const promote = vi.fn();
    const sendBack = vi.fn().mockResolvedValue({ kind: "accepted", status: "sent_back" });
    render(<PromotionDecision requestId="req-1" canPromote actions={{ promote, sendBack }} />);
    expect(screen.getByRole("button", { name: "Send back" })).toBeDisabled();
    await user.type(
      screen.getByLabelText("What should change before this goes live"),
      "Tighten the memo wording",
    );
    await user.click(screen.getByRole("button", { name: "Send back" }));
    expect(sendBack).toHaveBeenCalledWith("req-1", "Tighten the memo wording");
  });
});
