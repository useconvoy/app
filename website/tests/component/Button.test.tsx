import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Button } from "@/components/Button";

describe("Button", () => {
  it("defaults to a non-submitting primary button", () => {
    render(<Button>Approve plan</Button>);
    const button = screen.getByRole("button", { name: "Approve plan" });
    expect(button).toHaveAttribute("type", "button");
    expect(button).toHaveClass("bg-pine");
  });

  it("styles secondary and danger variants from tokens", () => {
    const { rerender } = render(<Button variant="secondary">Send back</Button>);
    expect(screen.getByRole("button")).toHaveClass("bg-card");

    rerender(<Button variant="danger">Send back</Button>);
    expect(screen.getByRole("button")).toHaveClass("bg-fail");
  });

  it("announces and enforces the in-flight state", () => {
    render(<Button pending>Approve plan</Button>);
    const button = screen.getByRole("button", { name: "Approve plan" });
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(button).toBeDisabled();
  });

  it("keeps the label in the accessibility tree while busy so the button holds its width", () => {
    // Hidden, not removed. Dropping the label collapses the button mid-click
    // and the row it sits in reflows under the pointer.
    const { rerender } = render(<Button>Approve plan</Button>);
    const idle = screen.getByText("Approve plan");
    expect(idle).not.toHaveClass("invisible");

    rerender(<Button pending>Approve plan</Button>);
    expect(screen.getByText("Approve plan")).toHaveClass("invisible");
  });

  it("carries no busy affordance when idle", () => {
    const { container } = render(<Button>Approve plan</Button>);
    expect(screen.getByRole("button")).not.toHaveAttribute("aria-busy");
    expect(container.querySelector(".spinner-delayed")).toBeNull();
  });

  it("leaves an explicitly disabled button disabled without claiming it is busy", () => {
    render(<Button disabled>Approve plan</Button>);
    const button = screen.getByRole("button", { name: "Approve plan" });
    expect(button).toBeDisabled();
    expect(button).not.toHaveAttribute("aria-busy");
  });
});
