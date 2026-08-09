import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const navigation = vi.hoisted(() => ({ refresh: vi.fn(), push: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: navigation.refresh, push: navigation.push }),
}));

import { CreateEnvironmentModal } from "@/app/(portal)/app/environments/CreateEnvironmentModal";
import { EnvironmentCards } from "@/app/(portal)/app/environments/EnvironmentCards";
import type { ExecutionEnvironment } from "@/lib/api/environments";

const workspaces = [{ id: "workspace-1", name: "Operations", systemCount: 3 }];

beforeEach(() => {
  navigation.refresh.mockClear();
  navigation.push.mockClear();
});

describe("CreateEnvironmentModal", () => {
  it("creates a browser-enabled default environment and opens its detail", async () => {
    const user = userEvent.setup();
    const create = vi.fn().mockResolvedValue({ id: "environment-1" });
    render(<CreateEnvironmentModal workspaces={workspaces} create={create} />);

    await user.click(screen.getByRole("button", { name: "New environment" }));
    const dialog = screen.getByRole("dialog");
    await user.type(within(dialog).getByLabelText("Name"), "Production operations");
    await user.type(
      within(dialog).getByLabelText("Purpose"),
      "Runs approved routines against live systems",
    );
    await user.click(within(dialog).getByLabelText("Make a browser available to runs"));
    await user.type(
      within(dialog).getByLabelText("Allowed domains"),
      "app.example.com, docs.example.com",
    );
    await user.click(within(dialog).getByRole("button", { name: "Create environment" }));

    expect(create).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      name: "Production operations",
      purpose: "Runs approved routines against live systems",
      sandboxTemplate: "convoy-devbox-python",
      browserEnabled: true,
      allowedDomains: ["app.example.com", "docs.example.com"],
      persistBrowserProfile: true,
      makeDefault: true,
    });
    expect(navigation.push).toHaveBeenCalledWith("/app/environments/environment-1");
    expect(navigation.refresh).toHaveBeenCalled();
  });

  it("requires a workspace, name, purpose, and compute template", async () => {
    const user = userEvent.setup();
    const create = vi.fn();
    render(<CreateEnvironmentModal workspaces={[]} create={create} />);

    expect(screen.getByRole("button", { name: "New environment" })).toBeDisabled();

    render(<CreateEnvironmentModal workspaces={workspaces} create={create} />);
    await user.click(screen.getAllByRole("button", { name: "New environment" })[1]!);
    const dialog = screen.getByRole("dialog");
    await user.clear(within(dialog).getByLabelText("Compute template"));
    await user.click(within(dialog).getByRole("button", { name: "Create environment" }));

    expect(create).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("Give the environment a name.");
    expect(screen.getByRole("alert")).toHaveTextContent("Describe what runs here.");
    expect(screen.getByRole("alert")).toHaveTextContent("Choose a compute template.");
  });
});

describe("EnvironmentCards", () => {
  it("shows workspace, compute, browser policy, and the default marker", () => {
    const environment: ExecutionEnvironment = {
      id: "environment-1",
      workspaceId: "workspace-1",
      registryEnvironmentId: "registry-environment-1",
      name: "Production operations",
      purpose: "Runs approved routines",
      productionBindingId: "registry-environment-1",
      rehearsalBindingId: "registry-environment-1/sandbox",
      sandboxTemplate: "convoy-devbox-python",
      browserPolicy: { allowedDomains: ["app.example.com"], persistProfile: true },
      persistence: "automatic",
      version: 1,
      isDefault: true,
      createdAt: "2026-08-09T00:00:00.000Z",
      updatedAt: "2026-08-09T00:00:00.000Z",
    };

    render(
      <EnvironmentCards
        environments={[environment]}
        workspaceNames={{ "workspace-1": "Operations" }}
      />,
    );

    expect(screen.getByRole("link", { name: /Production operations/ })).toHaveAttribute(
      "href",
      "/app/environments/environment-1",
    );
    expect(screen.getByText("Operations")).toBeInTheDocument();
    expect(screen.getByText("convoy-devbox-python")).toBeInTheDocument();
    expect(screen.getByText("1 allowed domains")).toBeInTheDocument();
    expect(screen.getByText("Default")).toBeInTheDocument();
  });
});
