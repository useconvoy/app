import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RehearsalBanner } from "@/components/RehearsalBanner";

import { expectNoAxeViolations } from "../support/axe";

describe("RehearsalBanner", () => {
  it("renders the rehearsal copy in the graphite dashed treatment", () => {
    const { container } = render(<RehearsalBanner exitHref="/app" />);
    expect(
      screen.getByText("You are looking at a rehearsal. Nothing here touches live systems."),
    ).toBeInTheDocument();
    expect(container.querySelector(".border-dashed")).not.toBeNull();
    expect(container.querySelector(".bg-graphite-soft")).not.toBeNull();
  });

  it("offers the exit rehearsal action", () => {
    render(<RehearsalBanner exitHref="/app" />);
    const exit = screen.getByRole("link", { name: "Exit rehearsal" });
    expect(exit).toHaveAttribute("href", "/app");
    expect(exit).toHaveClass("uppercase");
  });

  it("has no axe violations", async () => {
    const { container } = render(<RehearsalBanner exitHref="/app" />);
    await expectNoAxeViolations(container);
  });
});
