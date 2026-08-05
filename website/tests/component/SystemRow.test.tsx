import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SystemRow } from "@/components/SystemRow";

describe("SystemRow", () => {
  it("labels grants through the lexicon", () => {
    const { rerender } = render(
      <ul>
        <SystemRow name="Okta" grant="read" />
      </ul>,
    );
    expect(screen.getByText("Okta")).toBeInTheDocument();
    expect(screen.getByText("View only")).toBeInTheDocument();

    rerender(
      <ul>
        <SystemRow name="Workday" grant="write" />
      </ul>,
    );
    expect(screen.getByText("Can update")).toBeInTheDocument();
  });

  it("notes stand-ins in plain language", () => {
    render(
      <ul>
        <SystemRow name="Outbox" grant="write" standInFor="Email" />
      </ul>,
    );
    expect(screen.getByText("Stand-in for Email")).toBeInTheDocument();
  });
});
