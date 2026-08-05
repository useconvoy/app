import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Button } from "@/components/Button";
import { EmptyState } from "@/components/EmptyState";

describe("EmptyState", () => {
  it("renders title, body, and an optional action", () => {
    render(
      <EmptyState
        title="No runs yet"
        body="Runs appear here as soon as a routine starts work."
        action={<Button variant="secondary">See routines</Button>}
      />,
    );
    expect(screen.getByText("No runs yet")).toBeInTheDocument();
    expect(
      screen.getByText("Runs appear here as soon as a routine starts work."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "See routines" })).toBeInTheDocument();
  });
});
