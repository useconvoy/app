import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AgentCards } from "@/app/(portal)/app/agents/AgentCards";
import type { Agent } from "@/lib/api/environments";

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
