import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusChip } from "@/components/StatusChip";

import { expectNoAxeViolations } from "../support/axe";

describe("StatusChip", () => {
  it("labels every status through the lexicon, never color alone", () => {
    const { rerender } = render(<StatusChip status="blocked_on_human" />);
    expect(screen.getByText("Held for you")).toBeInTheDocument();

    rerender(<StatusChip status="running" />);
    expect(screen.getByText("Running")).toBeInTheDocument();

    rerender(<StatusChip status="landed" />);
    expect(screen.getByText("Landed")).toBeInTheDocument();

    rerender(<StatusChip status="failed" />);
    expect(screen.getByText("Failed")).toBeInTheDocument();

    rerender(<StatusChip status="paused" />);
    expect(screen.getByText("Paused")).toBeInTheDocument();
  });

  it("pulses only while running", () => {
    const { container, rerender } = render(<StatusChip status="running" />);
    expect(container.querySelector(".pulse-live")).not.toBeNull();

    rerender(<StatusChip status="landed" />);
    expect(container.querySelector(".pulse-live")).toBeNull();
  });

  it("renders the rehearsal variant dashed in graphite with a label", () => {
    const { container } = render(<StatusChip status="running" rehearsal />);
    expect(screen.getByText("rehearsal")).toBeInTheDocument();
    expect(container.querySelector(".border-dashed")).not.toBeNull();
    expect(container.firstChild).toMatchSnapshot();
  });

  it("has no axe violations", async () => {
    const { container } = render(
      <div>
        <StatusChip status="running" />
        <StatusChip status="blocked_on_human" />
        <StatusChip status="failed" rehearsal />
      </div>,
    );
    await expectNoAxeViolations(container);
  });
});
