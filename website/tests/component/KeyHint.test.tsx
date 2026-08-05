import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { KeyHint } from "@/components/KeyHint";

describe("KeyHint", () => {
  it("renders each key as a key cap with its label", () => {
    const { container } = render(
      <KeyHint
        hints={[
          { keyName: "A", label: "approve" },
          { keyName: "E", label: "edit" },
          { keyName: "R", label: "reject" },
        ]}
      />,
    );
    expect(container.querySelectorAll("kbd")).toHaveLength(3);
    expect(screen.getByText("A")).toBeInTheDocument();
    expect(screen.getByText("approve")).toBeInTheDocument();
    expect(screen.getByText("E")).toBeInTheDocument();
    expect(screen.getByText("edit")).toBeInTheDocument();
    expect(screen.getByText("R")).toBeInTheDocument();
    expect(screen.getByText("reject")).toBeInTheDocument();
  });
});
