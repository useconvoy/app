import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Chip } from "@/components/Chip";

describe("Chip", () => {
  it("renders its content in the requested tone", () => {
    render(<Chip tone="hold">Held for you</Chip>);
    expect(screen.getByText("Held for you")).toHaveClass("text-hold");
  });

  it("supports the dashed rehearsal treatment and mono facts", () => {
    render(
      <Chip tone="graphite" dashed mono>
        rehearsal
      </Chip>,
    );
    const chip = screen.getByText("rehearsal");
    expect(chip).toHaveClass("border-dashed");
    expect(chip).toHaveClass("font-mono");
  });
});
