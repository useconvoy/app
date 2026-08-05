import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { describe, expect, it } from "vitest";

import { TimelineRow } from "@/components/TimelineRow";

function renderRow(row: ReactElement) {
  return render(<ul>{row}</ul>);
}

describe("TimelineRow", () => {
  it("always shows the actor", () => {
    renderRow(<TimelineRow event="step_done" actor="J. Doe" at="2026-08-03T09:12:00" />);
    expect(screen.getByText("J. Doe")).toBeInTheDocument();
    expect(screen.getByText("Step finished")).toBeInTheDocument();
    expect(screen.getByText("AUG 3 · 9:12AM")).toBeInTheDocument();
  });

  it("keeps the actor even on compacted rows", () => {
    renderRow(
      <TimelineRow event="compaction_applied" actor="Quarterly user access review" at="2026-08-03T09:12:00" />,
    );
    expect(screen.getByText("Quarterly user access review")).toBeInTheDocument();
  });

  it("renders compaction collapsed and quiet, with no expandable payload", () => {
    const { container } = renderRow(
      <TimelineRow
        event="compaction_applied"
        actor="Quarterly user access review"
        at="2026-08-03T09:12:00"
        payload={{ notes: 12 }}
      />,
    );
    expect(screen.getByText("Notes tidied")).toBeInTheDocument();
    expect(container.querySelector("details")).toBeNull();
  });

  it("dual-timestamps rehearsal rows with virtual time primary", () => {
    const { container } = renderRow(
      <TimelineRow
        event="gate_answered"
        actor="R. Roe"
        at="2026-08-03T09:12:00"
        virtualAt="2026-08-14T10:00:00"
      />,
    );
    const virtualStamp = screen.getByText("AUG 14 · 10:00AM");
    const realStamp = screen.getByText("AUG 3 · 9:12AM");
    expect(virtualStamp).toBeInTheDocument();
    expect(realStamp).toBeInTheDocument();
    const html = container.innerHTML;
    expect(html.indexOf("AUG 14")).toBeLessThan(html.indexOf("AUG 3"));
  });

  it("exposes the payload behind an expandable mono affordance", async () => {
    const user = userEvent.setup();
    renderRow(
      <TimelineRow
        event="budget_warning"
        actor="Quarterly user access review"
        at="2026-08-03T09:12:00"
        payload={{ spent_usd: "33.10" }}
      />,
    );
    await user.click(screen.getByText("Details"));
    expect(screen.getByText(/33\.10/)).toBeInTheDocument();
  });
});
