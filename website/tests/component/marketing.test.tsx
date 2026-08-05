import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import MarketingLayout from "@/app/(marketing)/layout";
import HomePage from "@/app/(marketing)/page";
import PlatformPage from "@/app/(marketing)/platform/page";
import { DemoRequestForm } from "@/app/(marketing)/demo/demo-form";
import NotFound from "@/app/not-found";

const EM_DASH = /—/;

// Vitest runs without injected globals here, so the automatic
// testing-library cleanup never registers itself; do it explicitly.
afterEach(cleanup);

describe("marketing pages", () => {
  it("renders the home page in the marketing frame without em dashes", () => {
    const { container } = render(
      <MarketingLayout>
        <HomePage />
      </MarketingLayout>,
    );

    expect(
      screen.getByRole("heading", { level: 1, name: /routine work, done carefully/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /how a routine earns your trust/i }),
    ).toBeInTheDocument();
    for (const step of [
      /rehearse safely/i,
      /approve the plan/i,
      /it holds for your judgment/i,
    ]) {
      expect(
        screen.getByRole("heading", { level: 3, name: step }),
      ).toBeInTheDocument();
    }

    const nav = screen.getByRole("navigation", { name: /main/i });
    for (const label of ["Platform", "Solutions", "Security", "Company", "Writing"]) {
      expect(within(nav).getByRole("link", { name: label })).toBeInTheDocument();
    }
    expect(
      screen.getAllByRole("link", { name: /request a demo/i }).length,
    ).toBeGreaterThan(0);

    expect(container.textContent).not.toMatch(EM_DASH);
  });

  it("renders the platform page without em dashes", () => {
    const { container } = render(<PlatformPage />);

    expect(
      screen.getByRole("heading", { level: 1, name: /how convoy works/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /rehearsal copies/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /checkpoints/i }),
    ).toBeInTheDocument();

    expect(container.textContent).not.toMatch(EM_DASH);
  });

  it("shows validation errors when the demo form is submitted empty", async () => {
    const user = userEvent.setup();
    render(<DemoRequestForm />);

    await user.click(screen.getByRole("button", { name: /send request/i }));

    expect(
      await screen.findByText(/please fix the fields below/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/please tell us your name/i)).toBeInTheDocument();
    expect(
      screen.getByText(/please tell us your work email/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/please tell us where you work/i),
    ).toBeInTheDocument();
  });

  it("shows the confirmation state after a valid demo request", async () => {
    const user = userEvent.setup();
    const { container } = render(<DemoRequestForm />);

    await user.type(screen.getByLabelText(/your name/i), "J. Doe");
    await user.type(screen.getByLabelText(/work email/i), "j.doe@example.com");
    await user.type(screen.getByLabelText(/company/i), "Example Partners");
    await user.type(
      screen.getByLabelText(/what routine work do you want to hand off/i),
      "Our quarterly access review.",
    );
    await user.click(screen.getByRole("button", { name: /send request/i }));

    expect(
      await screen.findByText(/we received your request/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/J\. Doe/);

    expect(container.textContent).not.toMatch(EM_DASH);
  });

  it("renders the 404 page with a way home and no em dashes", () => {
    const { container } = render(<NotFound />);

    expect(
      screen.getByRole("heading", { level: 1, name: /this page does not exist/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /back to the home page/i }),
    ).toBeInTheDocument();

    expect(container.textContent).not.toMatch(EM_DASH);
  });
});
