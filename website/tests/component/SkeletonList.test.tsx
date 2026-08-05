import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SkeletonList } from "@/components/SkeletonList";

describe("SkeletonList", () => {
  it("announces loading and hides placeholder rows from assistive tech", () => {
    const { container } = render(<SkeletonList rows={3} />);
    const status = screen.getByRole("status", { name: "Loading" });
    expect(status).toHaveAttribute("aria-busy", "true");
    expect(container.querySelectorAll("[aria-hidden='true']")).toHaveLength(3);
  });
});
