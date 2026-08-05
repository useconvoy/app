import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { PlanVersionPanel, type PlanVersion } from "@/components/PlanVersionPanel";

const versions: PlanVersion[] = [
  {
    version: 1,
    steps: [
      "Pull the current list of people and their access",
      "Compare against the HR system and note differences",
      "Assemble the final review packet",
    ],
  },
  {
    version: 2,
    steps: [
      "Pull the current list of people and their access",
      "Compare against the HR system and note differences",
      "Chase anyone who has not responded",
      "Assemble the final review packet",
    ],
  },
];

describe("PlanVersionPanel", () => {
  it("renders added and removed steps as mono diff lines", () => {
    render(
      <PlanVersionPanel
        versions={[
          versions[0]!,
          { version: 2, steps: ["Pull the current list of people and their access", "Chase anyone who has not responded"] },
        ]}
      />,
    );
    expect(screen.getByText(/^\+ Chase anyone who has not responded$/)).toBeInTheDocument();
    expect(
      screen.getByText(/^- Compare against the HR system and note differences$/),
    ).toBeInTheDocument();
    expect(screen.getByText(/^- Assemble the final review packet$/)).toBeInTheDocument();
  });

  it("switches versions through tabs", async () => {
    const user = userEvent.setup();
    render(<PlanVersionPanel versions={versions} />);
    expect(screen.getByRole("tab", { name: "v2" })).toHaveAttribute("aria-selected", "true");

    await user.click(screen.getByRole("tab", { name: "v1" }));
    expect(screen.getByRole("tab", { name: "v1" })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByText(/^\+ Chase anyone who has not responded$/)).not.toBeInTheDocument();
  });

  it("stamps approved versions in mono", () => {
    render(
      <PlanVersionPanel
        versions={[{ ...versions[1]!, approvedAt: "2026-08-03T09:12:00" }]}
      />,
    );
    expect(screen.getByText(/APPROVED · AUG 3 · 9:12AM/)).toBeInTheDocument();
  });

  it("always approves and rejects the rendered version", async () => {
    const user = userEvent.setup();
    const onApprove = vi.fn();
    const onReject = vi.fn();
    render(<PlanVersionPanel versions={versions} onApprove={onApprove} onReject={onReject} />);

    await user.click(screen.getByRole("tab", { name: "v1" }));
    await user.click(screen.getByRole("button", { name: "Approve plan" }));
    expect(onApprove).toHaveBeenCalledWith(1);

    await user.click(screen.getByRole("button", { name: "Send back" }));
    await user.type(screen.getByLabelText("What should change"), "Chase step is missing");
    await user.click(screen.getByRole("button", { name: "Send back" }));
    expect(onReject).toHaveBeenCalledWith(1, "Chase step is missing");
  });

  it("renders the newer-version state with a review CTA and no retry", async () => {
    const onApprove = vi.fn();
    const onReviewNewer = vi.fn();
    const user = userEvent.setup();
    render(
      <PlanVersionPanel
        versions={versions}
        newerVersion={3}
        onApprove={onApprove}
        onReviewNewer={onReviewNewer}
      />,
    );

    expect(screen.getByText("This plan has a newer version")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve plan" })).not.toBeInTheDocument();
    expect(onApprove).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Review v3" }));
    expect(onReviewNewer).toHaveBeenCalledWith(3);
  });
});
