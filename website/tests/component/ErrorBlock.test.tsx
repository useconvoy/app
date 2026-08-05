import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Button } from "@/components/Button";
import { ErrorBlock } from "@/components/ErrorBlock";

describe("ErrorBlock", () => {
  it("says what happened and what to do", () => {
    render(
      <ErrorBlock
        whatHappened="This page could not load runs."
        whatToDo="Check your connection and try again."
        action={<Button variant="secondary">Try again</Button>}
      />,
    );
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("This page could not load runs.")).toBeInTheDocument();
    expect(screen.getByText("Check your connection and try again.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});
