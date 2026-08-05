import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const navigation = vi.hoisted(() => ({
  pathname: "/app",
  push: vi.fn(),
  refresh: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => navigation.pathname,
  useRouter: () => ({ push: navigation.push, refresh: navigation.refresh }),
  redirect: (url: string) => {
    throw new Error(`redirect:${url}`);
  },
}));

import { CommandPalette } from "@/components/shell/CommandPalette";
import { NavItems } from "@/components/shell/NavItems";
import { NavLink } from "@/components/shell/NavLink";

const routes = [
  { href: "/app", label: "Overview" },
  { href: "/app/checkpoints", label: "Checkpoints" },
  { href: "/app/runs", label: "Runs" },
];

beforeEach(() => {
  navigation.pathname = "/app";
  navigation.push.mockClear();
  navigation.refresh.mockClear();
});

afterEach(cleanup);

describe("CommandPalette", () => {
  it("opens on command-K and lists the nav routes", () => {
    render(<CommandPalette routes={routes} />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    fireEvent.keyDown(window, { key: "k", metaKey: true });

    const dialog = screen.getByRole("dialog");
    for (const route of routes) {
      expect(within(dialog).getByRole("button", { name: route.label })).toBeInTheDocument();
    }
  });

  it("opens on ctrl-K", () => {
    render(<CommandPalette routes={routes} />);
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("closes on escape and returns focus to the trigger", () => {
    render(<CommandPalette routes={routes} />);
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    const dialog = screen.getByRole("dialog");

    fireEvent.keyDown(dialog, { key: "Escape" });

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /jump to/i })).toHaveFocus();
  });

  it("moves with arrows and jumps on enter", async () => {
    const user = userEvent.setup();
    render(<CommandPalette routes={routes} />);
    fireEvent.keyDown(window, { key: "k", metaKey: true });

    expect(screen.getByRole("button", { name: "Overview" })).toHaveFocus();

    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("button", { name: "Checkpoints" })).toHaveFocus();

    await user.keyboard("{Enter}");
    expect(navigation.push).toHaveBeenCalledWith("/app/checkpoints");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("traps tab inside the dialog, wrapping around the route list", async () => {
    const user = userEvent.setup();
    render(<CommandPalette routes={routes} />);
    fireEvent.keyDown(window, { key: "k", metaKey: true });

    await user.keyboard("{Tab}");
    expect(screen.getByRole("button", { name: "Checkpoints" })).toHaveFocus();

    await user.keyboard("{Shift>}{Tab}{Tab}{/Shift}");
    expect(screen.getByRole("button", { name: "Runs" })).toHaveFocus();

    await user.keyboard("{Tab}");
    expect(screen.getByRole("button", { name: "Overview" })).toHaveFocus();
  });
});

describe("NavLink", () => {
  it("marks the current area active", () => {
    navigation.pathname = "/app/runs";
    render(<NavLink href="/app/runs" label="Runs" />);
    expect(screen.getByRole("link", { name: "Runs" })).toHaveAttribute("aria-current", "page");
  });

  it("treats a child route as inside the area", () => {
    navigation.pathname = "/app/runs/r123";
    render(<NavLink href="/app/runs" label="Runs" />);
    expect(screen.getByRole("link", { name: "Runs" })).toHaveAttribute("aria-current", "page");
  });

  it("keeps Overview scoped to exactly /app", () => {
    navigation.pathname = "/app/runs";
    render(<NavLink href="/app" label="Overview" />);
    expect(screen.getByRole("link", { name: "Overview" })).not.toHaveAttribute("aria-current");
  });
});

describe("NavItems", () => {
  it("shows every area to an admin", () => {
    const { container } = render(<NavItems role="admin" />);
    const labels = [
      "Overview",
      "Checkpoints",
      "Runs",
      "Routines",
      "Workspaces",
      "Routine evaluation",
      "Learning",
      "Logs",
      "Admin",
    ];
    for (const label of labels) {
      expect(screen.getByRole("link", { name: label })).toBeInTheDocument();
    }
    expect(container).toMatchSnapshot();
  });

  it("lenses a viewer down to the read-only areas", () => {
    const { container } = render(<NavItems role="viewer" />);
    for (const label of ["Overview", "Runs", "Routines", "Routine evaluation", "Learning"]) {
      expect(screen.getByRole("link", { name: label })).toBeInTheDocument();
    }
    for (const label of ["Checkpoints", "Workspaces", "Logs", "Admin"]) {
      expect(screen.queryByRole("link", { name: label })).not.toBeInTheDocument();
    }
    expect(container).toMatchSnapshot();
  });
});
