import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { FeedbackComposer } from "@/components/FeedbackComposer";
import { FeedbackStream, type FeedbackStreamItem } from "@/components/FeedbackStream";
import { expectNoAxeViolations } from "../support/axe";

describe("FeedbackComposer", () => {
  it("requires a star rating before a helpful rating submits", () => {
    const action = vi.fn();
    render(<FeedbackComposer action={action} />);
    fireEvent.submit(screen.getByRole("form", { name: "Leave feedback" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Pick a star rating before sending.");
    expect(action).not.toHaveBeenCalled();
  });

  it("requires a body before a comment submits", () => {
    const action = vi.fn();
    render(<FeedbackComposer action={action} />);
    fireEvent.click(screen.getByRole("radio", { name: "Comment" }));
    fireEvent.submit(screen.getByRole("form", { name: "Leave feedback" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Write your feedback before sending.");
    expect(action).not.toHaveBeenCalled();
  });

  it("requires a body before a correction submits", () => {
    const action = vi.fn();
    render(<FeedbackComposer action={action} />);
    fireEvent.click(screen.getByRole("radio", { name: "Correction" }));
    fireEvent.submit(screen.getByRole("form", { name: "Leave feedback" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Write your feedback before sending.");
    expect(action).not.toHaveBeenCalled();
  });

  it("clears the error once a rating is picked", () => {
    render(<FeedbackComposer action={vi.fn()} />);
    fireEvent.submit(screen.getByRole("form", { name: "Leave feedback" }));
    expect(screen.getByRole("alert")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "4 stars: Helpful" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("renders disabled with a plain note when there is no run to attach to", () => {
    render(<FeedbackComposer disabledNote="Feedback attaches to a run. This routine has not run yet." />);
    expect(
      screen.getByText("Feedback attaches to a run. This routine has not run yet."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send feedback" })).toBeDisabled();
    expect(screen.getByLabelText("Your feedback")).toBeDisabled();
  });

  it("labels every star with its plain meaning", () => {
    render(<FeedbackComposer action={vi.fn()} />);
    expect(screen.getByRole("radio", { name: "1 star: Not helpful" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "5 stars: Very helpful" })).toBeInTheDocument();
  });

  it("has no axe violations", async () => {
    const { container } = render(<FeedbackComposer action={vi.fn()} />);
    await expectNoAxeViolations(container);
  });
});

const items: FeedbackStreamItem[] = [
  {
    id: "feedback-1",
    kind: "rating",
    rating: 4,
    body: null,
    authorName: "J. Doe",
    learningStatus: "new",
    createdAt: "2026-08-03T09:12:00",
  },
  {
    id: "feedback-2",
    kind: "comment",
    rating: null,
    body: "The memo for the contractor account named the wrong manager.",
    authorName: "R. Roe",
    learningStatus: "queued",
    createdAt: "2026-08-02T15:40:00",
  },
  {
    id: "feedback-3",
    kind: "correction",
    rating: null,
    body: "Reminders should go to the person's manager after the second miss.",
    authorName: "M. Smith",
    learningStatus: "consumed",
    createdAt: "2026-07-28T11:05:00",
  },
];

describe("FeedbackStream", () => {
  it("renders author, kind, body, and a friendly date per item", () => {
    render(<FeedbackStream items={items} />);
    expect(screen.getByText("J. Doe")).toBeInTheDocument();
    expect(screen.getByText("4 of 5")).toBeInTheDocument();
    expect(
      screen.getByText("The memo for the contractor account named the wrong manager."),
    ).toBeInTheDocument();
    expect(screen.getByText("AUG 3 · 9:12AM")).toBeInTheDocument();
  });

  it("renders every learning status through the lexicon, quietly", () => {
    render(<FeedbackStream items={items} />);
    expect(screen.getByText("Noted")).toBeInTheDocument();
    expect(screen.getByText("Queued for learning")).toBeInTheDocument();
    expect(screen.getByText("Applied")).toBeInTheDocument();
  });

  it("shows an empty state when nothing has been said", () => {
    render(<FeedbackStream items={[]} emptyBody="Feedback lands here." />);
    expect(screen.getByText("No feedback yet")).toBeInTheDocument();
    expect(screen.getByText("Feedback lands here.")).toBeInTheDocument();
  });
});
