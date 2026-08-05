import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { HowItFitsStrip } from "@/components/HowItFitsStrip";

describe("HowItFitsStrip", () => {
  it("renders the plain-language strip when shown", () => {
    render(<HowItFitsStrip showHowItFits />);
    expect(screen.getByText("How it fits together")).toBeInTheDocument();
    expect(screen.getByText(/A routine is a job that runs on a schedule/)).toBeInTheDocument();
  });

  it("renders nothing when hidden", () => {
    const { container } = render(<HowItFitsStrip showHowItFits={false} />);
    expect(container).toBeEmptyDOMElement();
  });
});
