import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { BellPanelList, type BellPanelItem } from "@/components/BellPanelList";

const items: BellPanelItem[] = [
  {
    id: "run-1:12",
    notificationClass: "checkpoint_opened",
    title: "Approve the exception memo for the finance group",
    actionUrl: "/app/runs/run-1/steps/s3/respond",
    at: "2026-08-03T09:12:00",
  },
  {
    id: "run-2:4",
    notificationClass: "budget_warning",
    title: "Vendor due-diligence document refresh",
    actionUrl: "/app/runs/run-2",
  },
];

describe("BellPanelList", () => {
  it("deep-links each item to its typed action URL", () => {
    render(<BellPanelList items={items} checkpointsUrl="/app/checkpoints" />);
    const links = screen.getAllByRole("link");
    expect(links[0]).toHaveAttribute("href", "/app/runs/run-1/steps/s3/respond");
    expect(links[1]).toHaveAttribute("href", "/app/runs/run-2");
  });

  it("labels each item through the lexicon", () => {
    render(<BellPanelList items={items} checkpointsUrl="/app/checkpoints" />);
    expect(screen.getByText("Held for you")).toBeInTheDocument();
    expect(screen.getByText("Approaching budget")).toBeInTheDocument();
  });

  it("links the footer to the checkpoints page", () => {
    render(<BellPanelList items={items} checkpointsUrl="/app/checkpoints" />);
    const footer = screen.getByRole("link", { name: "Open checkpoints" });
    expect(footer).toHaveAttribute("href", "/app/checkpoints");
  });

  it("renders the calm state when nothing needs anyone", () => {
    render(<BellPanelList items={[]} checkpointsUrl="/app/checkpoints" />);
    expect(screen.getByText("All quiet. Nothing needs you right now.")).toBeInTheDocument();
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });
});
