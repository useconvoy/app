import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Chip } from "@/components/Chip";

describe("Chip", () => {
  it("renders its content in the requested tone", () => {
    render(<Chip tone="hold">Held for you</Chip>);
    const chip = screen.getByText("Held for you");
    // The fill token measures 3.35:1 on its own soft ground, under the 4.5:1
    // this label needs. The -text variant is the one that carries glyphs.
    expect(chip).toHaveClass("text-hold-text");
    expect(chip).not.toHaveClass("text-hold");
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
