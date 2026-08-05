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
});
