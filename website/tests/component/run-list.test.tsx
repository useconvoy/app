import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import RunsLoading from "@/app/(portal)/app/runs/loading";
import { RunsList, type RunListRow } from "@/app/(portal)/app/runs/runs-list";
import { routines } from "@/lib/fixtures/world";
import { expectNoAxeViolations } from "../support/axe";

const accessReview = routines[0]!;
const vendorCheck = routines[1]!;
const attestation = routines[2]!;

const rows: RunListRow[] = [
  {
    id: "run-list-a1",
    goal: accessReview.name,
    status: "running",
    done: 1,
    total: 5,
    spentUsd: "12.40",
    rehearsal: false,
  },
  {
    id: "run-list-b2",
    goal: vendorCheck.name,
    status: "blocked_on_human",
    done: 2,
    total: 4,
    spentUsd: "33.10",
    rehearsal: false,
  },
  {
    id: "run-list-c3",
    goal: attestation.name,
    status: "completed",
    done: 4,
    total: 4,
    spentUsd: "6.00",
    rehearsal: true,
  },
];

describe("RunsList", () => {
  it("renders run, status, progress, and spent columns without run ids", () => {
    render(<RunsList rows={rows} />);
    const row = screen.getByText(accessReview.name).closest("tr") as HTMLElement;
    expect(within(row).getByText("Running")).toBeInTheDocument();
    expect(within(row).getByText("1/5")).toBeInTheDocument();
    expect(within(row).getByText("$12.40")).toBeInTheDocument();
    // Ids ride hrefs only; no run id ever renders as text.
    expect(screen.queryByText(/run-list/)).not.toBeInTheDocument();
  });

  it("links each row to its run detail", () => {
    render(<RunsList rows={rows} />);
    expect(screen.getByRole("link", { name: accessReview.name })).toHaveAttribute(
      "href",
      "/app/runs/run-list-a1",
    );
  });

  it("filters by status group, with held covering the stuck states", async () => {
    const user = userEvent.setup();
    render(<RunsList rows={rows} />);
    await user.click(screen.getByRole("button", { name: "Held" }));
    expect(screen.getByText(vendorCheck.name)).toBeInTheDocument();
    expect(screen.queryByText(accessReview.name)).not.toBeInTheDocument();
    expect(screen.queryByText(attestation.name)).not.toBeInTheDocument();
  });

  it("toggles between production and rehearsal runs", async () => {
    const user = userEvent.setup();
    render(<RunsList rows={rows} />);
    await user.click(screen.getByRole("button", { name: "Rehearsals" }));
    expect(screen.getByText(attestation.name)).toBeInTheDocument();
    expect(screen.queryByText(accessReview.name)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Production" }));
    expect(screen.getByText(accessReview.name)).toBeInTheDocument();
    expect(screen.queryByText(attestation.name)).not.toBeInTheDocument();
  });

  it("searches over goal text", async () => {
    const user = userEvent.setup();
    render(<RunsList rows={rows} />);
    await user.type(screen.getByRole("searchbox", { name: "Search runs" }), "vendor");
    expect(screen.getByText(vendorCheck.name)).toBeInTheDocument();
    expect(screen.queryByText(accessReview.name)).not.toBeInTheDocument();
  });

  it("gives rehearsal rows the pencil treatment and label", () => {
    render(<RunsList rows={rows} />);
    const rehearsalRow = screen.getByText(attestation.name).closest("tr");
    expect(rehearsalRow).toHaveAttribute("data-rehearsal", "true");
    expect(within(rehearsalRow as HTMLElement).getByText("rehearsal")).toBeInTheDocument();
    const productionRow = screen.getByText(accessReview.name).closest("tr");
    expect(productionRow).not.toHaveAttribute("data-rehearsal");
    // Production is the unlabeled default: no production tag on rows.
    expect(within(productionRow as HTMLElement).queryByText(/production/i)).not.toBeInTheDocument();
  });

  it("shows the empty state with the start action when there are no runs", () => {
    render(<RunsList rows={[]} startAction={<button type="button">Start a rehearsal run</button>} />);
    expect(screen.getByText("No runs yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start a rehearsal run" })).toBeInTheDocument();
  });

  it("shows a narrower empty state when filters match nothing", async () => {
    const user = userEvent.setup();
    render(<RunsList rows={rows} />);
    await user.type(screen.getByRole("searchbox", { name: "Search runs" }), "zzz");
    expect(screen.getByText("No runs match")).toBeInTheDocument();
  });

  it("has no axe violations", async () => {
    const { container } = render(<RunsList rows={rows} />);
    await expectNoAxeViolations(container);
  });
});

describe("RunsLoading", () => {
  it("renders a list skeleton, never a full-page spinner", () => {
    render(<RunsLoading />);
    expect(screen.getByRole("status", { name: "Loading" })).toBeInTheDocument();
  });
});
