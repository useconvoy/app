import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const navigation = vi.hoisted(() => ({ refresh: vi.fn(), push: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: navigation.refresh, push: navigation.push }),
}));

import { CreateAgentModal } from "@/app/(portal)/app/agents/CreateAgentModal";
import { AgentCards } from "@/app/(portal)/app/agents/AgentCards";
import type { Agent } from "@/lib/api/environments";

const workspaces = [{ id: "workspace-1", name: "Operations", systemCount: 3 }];

beforeEach(() => {
  navigation.refresh.mockClear();
  navigation.push.mockClear();
});

describe("CreateAgentModal", () => {
  it("creates a browser-enabled Agent with its goal and opens its detail", async () => {
    const user = userEvent.setup();
    const create = vi.fn().mockResolvedValue({ id: "environment-1" });
    render(<CreateAgentModal workspaces={workspaces} create={create} />);

    await user.click(screen.getByRole("button", { name: "New agent" }));
    const dialog = screen.getByRole("dialog");
    await user.type(within(dialog).getByLabelText("Name"), "Production operations");
    await user.type(
      within(dialog).getByLabelText("Purpose"),
      "Owns revenue operations work",
    );
    await user.type(
      within(dialog).getByLabelText("Goal"),
      "Review renewals and post the recovery summary",
    );
    await user.type(
      within(dialog).getByLabelText(/Instructions/),
      "Read the sheet{enter}Post the summary",
    );
    await user.click(within(dialog).getByLabelText("Give this agent browser access"));
    await user.type(
      within(dialog).getByLabelText("Allowed domains"),
      "app.example.com, docs.example.com",
    );
    await user.click(within(dialog).getByRole("button", { name: "Create agent" }));

    expect(create).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      name: "Production operations",
      purpose: "Owns revenue operations work",
      goal: "Review renewals and post the recovery summary",
      planSteps: ["Read the sheet", "Post the summary"],
      scheduleDescription: "",
      budgetCapUsd: 75,
      sandboxTemplate: "convoy-devbox-python",
      browserEnabled: true,
      allowedDomains: ["app.example.com", "docs.example.com"],
      persistBrowserProfile: true,
      makeDefault: false,
    });
    expect(navigation.push).toHaveBeenCalledWith("/app/agents/environment-1");
    expect(navigation.refresh).toHaveBeenCalled();
  });

  it("requires a workspace, name, purpose, goal, and compute template", async () => {
    const user = userEvent.setup();
    const create = vi.fn();
    render(<CreateAgentModal workspaces={[]} create={create} />);

    expect(screen.getByRole("button", { name: "New agent" })).toBeDisabled();

    render(<CreateAgentModal workspaces={workspaces} create={create} />);
    await user.click(screen.getAllByRole("button", { name: "New agent" })[1]!);
    const dialog = screen.getByRole("dialog");
    await user.clear(within(dialog).getByLabelText("Compute template"));
    await user.click(within(dialog).getByRole("button", { name: "Create agent" }));

    expect(create).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("Give the agent a name.");
    expect(screen.getByRole("alert")).toHaveTextContent("Describe what the agent does.");
    expect(screen.getByRole("alert")).toHaveTextContent("Describe the outcome the agent owns.");
    expect(screen.getByRole("alert")).toHaveTextContent("Choose a compute template.");
  });
});

describe("AgentCards", () => {
  it("shows workspace, goal, runtime, browser policy, and budget", () => {
    const agent: Agent = {
      id: "environment-1",
      workspaceId: "workspace-1",
      registryEnvironmentId: "registry-environment-1",
      name: "Production operations",
      purpose: "Owns revenue operations work",
      productionBindingId: "registry-environment-1",
      rehearsalBindingId: "registry-environment-1/sandbox",
      sandboxTemplate: "convoy-devbox-python",
      browserPolicy: { allowedDomains: ["app.example.com"], persistProfile: true },
      persistence: "automatic",
      goal: "Review renewals and post the recovery summary",
      systems: ["document_store", "messaging"],
      budgetCapUsd: "75.00",
      planSteps: ["Read the sheet", "Post the summary"],
      scheduleDescription: "Mondays at 9am",
      sourceEntryId: null,
      sourceVersion: null,
      automationConfigured: true,
      version: 1,
      isDefault: true,
      createdAt: "2026-08-09T00:00:00.000Z",
      updatedAt: "2026-08-09T00:00:00.000Z",
    };

    render(
      <AgentCards
        agents={[agent]}
        workspaceNames={{ "workspace-1": "Operations" }}
      />,
    );

    expect(screen.getByRole("link", { name: /Production operations/ })).toHaveAttribute(
      "href",
      "/app/agents/environment-1",
    );
    expect(screen.getByText("Operations")).toBeInTheDocument();
    expect(screen.getByText("convoy-devbox-python")).toBeInTheDocument();
    expect(screen.getByText("1 allowed domains")).toBeInTheDocument();
    expect(screen.getByText("Review renewals and post the recovery summary")).toBeInTheDocument();
    expect(screen.getByText("$75 per run")).toBeInTheDocument();
  });
});
