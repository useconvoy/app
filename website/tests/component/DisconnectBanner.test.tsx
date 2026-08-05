import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DisconnectBanner } from "@/components/DisconnectBanner";

describe("DisconnectBanner", () => {
  it("surfaces the disconnect and the last-event time", () => {
    render(<DisconnectBanner lastEventAt="2026-08-03T09:12:00" />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("Connection lost. Reconnecting")).toBeInTheDocument();
    expect(screen.getByText("Last update AUG 3 · 9:12AM")).toBeInTheDocument();
  });

  it("still renders without a last-event time", () => {
    render(<DisconnectBanner />);
    expect(screen.getByText("Connection lost. Reconnecting")).toBeInTheDocument();
  });
});
