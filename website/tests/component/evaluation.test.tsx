import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ScorecardTable } from "@/components/ScorecardTable";
import { TrajectoryList } from "@/components/TrajectoryList";
import { TrendLine, trendSummary } from "@/components/TrendLine";
import { fixtureScorecards, fixtureTrajectories, fixtureTrends } from "@/lib/fixtures/evals";
import { expectNoAxeViolations } from "../support/axe";

const accessTrend = fixtureTrends["routine-access-review"]!;

describe("TrendLine", () => {
  it("summarizes the trend in a plain accessible sentence", () => {
    render(<TrendLine points={accessTrend} title="Quarterly user access review" />);
    expect(screen.getByText("Test score 91, up 19 points over 8 runs")).toBeInTheDocument();
    expect(
      screen.getByRole("group", {
        name: "Quarterly user access review: Test score 91, up 19 points over 8 runs",
      }),
    ).toBeInTheDocument();
  });

  it("words falling and steady trends honestly", () => {
    expect(
      trendSummary([
        { at: "2026-07-01T09:00:00Z", score: 90 },
        { at: "2026-07-08T09:00:00Z", score: 86 },
      ]),
    ).toBe("Test score 86, down 4 points over 2 runs");
    expect(
      trendSummary([
        { at: "2026-07-01T09:00:00Z", score: 85 },
        { at: "2026-07-08T09:00:00Z", score: 85 },
      ]),
    ).toBe("Test score 85, steady over 2 runs");
    expect(trendSummary([{ at: "2026-07-01T09:00:00Z", score: 79 }])).toBe(
      "Test score 79 from the first scored run",
    );
  });

  it("renders a plain sentence instead of a chart when nothing is scored", () => {
    render(<TrendLine points={[]} title="Unscored routine" />);
    expect(screen.getByText("No test scores yet")).toBeInTheDocument();
  });

  it("has no axe violations", async () => {
    const { container } = render(<TrendLine points={accessTrend} title="Quarterly user access review" />);
    await expectNoAxeViolations(container);
  });
});

describe("TrajectoryList", () => {
  it("flags rehearsal rows with the dashed treatment and a label", () => {
    render(<TrajectoryList items={fixtureTrajectories["routine-access-review"]!} />);
    const rehearsalChips = screen.getAllByText("rehearsal");
    expect(rehearsalChips).toHaveLength(3);
    const rows = screen.getAllByRole("listitem");
    expect(rows.filter((row) => row.className.includes("border-dashed"))).toHaveLength(3);
  });

  it("leaves production rows unlabeled", () => {
    render(<TrajectoryList items={fixtureTrajectories["routine-access-review"]!} />);
    const productionRow = screen
      .getByText("Quarterly review landed with all sign-offs collected")
      .closest("li")!;
    expect(productionRow.className).not.toContain("border-dashed");
    expect(productionRow.textContent).not.toContain("rehearsal");
  });
});

describe("ScorecardTable", () => {
  const scorecard = fixtureScorecards["run-eval-ar-8"]!;

  it("labels pass and fail chips with words, never color alone", () => {
    render(
      <ScorecardTable
        scorecard={scorecard}
        headline="Reconciled every difference and chased both non-responders on time"
        at="2026-08-04T09:10:00Z"
        rehearsal
      />,
    );
    expect(screen.getAllByText("Pass")).toHaveLength(3);
    expect(screen.getAllByText("Fail")).toHaveLength(1);
    expect(screen.getByText("91")).toBeInTheDocument();
  });

  it("renders each criterion with its note", () => {
    render(<ScorecardTable scorecard={scorecard} headline="A scored run" />);
    expect(screen.getByText("Finds every access difference")).toBeInTheDocument();
    expect(screen.getByText("One appendix was out of order")).toBeInTheDocument();
  });

  it("has no axe violations", async () => {
    const { container } = render(
      <ScorecardTable scorecard={scorecard} headline="A scored run" rehearsal />,
    );
    await expectNoAxeViolations(container);
  });
});
