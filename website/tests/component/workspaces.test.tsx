import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const navigation = vi.hoisted(() => ({ refresh: vi.fn(), push: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: navigation.refresh, push: navigation.push }),
}));

import {
  CreateWorkspaceModal,
  type SystemOption,
} from "@/app/(portal)/app/workspaces/CreateWorkspaceModal";
import { WorkspaceCards } from "@/app/(portal)/app/workspaces/WorkspaceCards";
import { WorkspaceDetail } from "@/app/(portal)/app/workspaces/[workspaceId]/WorkspaceDetail";
import { fixtureWorkspaces } from "@/lib/fixtures/environments";

const systems: SystemOption[] = [
  { id: "document_store", displayName: "Document store", sideEffecting: false, standInNote: null },
  {
    id: "messaging",
    displayName: "Messaging",
    sideEffecting: true,
    standInNote: "Stand-in for Messaging: messages are held in the outbox instead of being sent.",
  },
];

beforeEach(() => {
  navigation.refresh.mockClear();
});

async function openModalAndFill(create: (payload: unknown) => Promise<{ id: string }>) {
  const user = userEvent.setup();
  render(<CreateWorkspaceModal systems={systems} create={create as never} />);
  await user.click(screen.getByRole("button", { name: "New workspace" }));
  const dialog = screen.getByRole("dialog");
  await user.type(within(dialog).getByLabelText("Name"), "Finance workspace");
  await user.type(within(dialog).getByLabelText("Purpose"), "Invoicing checks run here.");
  return { user, dialog };
}

describe("CreateWorkspaceModal", () => {
  it("automatically isolates side-effecting writes for rehearsal runs", async () => {
    const create = vi.fn().mockResolvedValue({ id: "workspace-x" });
    const { user, dialog } = await openModalAndFill(create);

    await user.click(within(dialog).getByLabelText("Messaging"));
    await user.selectOptions(within(dialog).getByRole("combobox"), "write");
    expect(
      within(dialog).getByText(/Rehearsal runs isolate writes automatically/),
    ).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Create workspace" }));

    expect(create).toHaveBeenCalledWith({
      name: "Finance workspace",
      purpose: "Invoicing checks run here.",
      systems: [{ systemId: "messaging", scope: "write", useStandIn: true }],
    });
    expect(navigation.refresh).toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("lets a read grant on a side-effecting system through without a stand-in", async () => {
    const create = vi.fn().mockResolvedValue({ id: "workspace-x" });
    const { user, dialog } = await openModalAndFill(create);

    await user.click(within(dialog).getByLabelText("Messaging"));
    await user.click(within(dialog).getByRole("button", { name: "Create workspace" }));

    expect(create).toHaveBeenCalledWith({
      name: "Finance workspace",
      purpose: "Invoicing checks run here.",
      systems: [{ systemId: "messaging", scope: "read", useStandIn: false }],
    });
  });

  it("requires a name, a purpose, and at least one system", async () => {
    const create = vi.fn();
    const user = userEvent.setup();
    render(<CreateWorkspaceModal systems={systems} create={create} />);
    await user.click(screen.getByRole("button", { name: "New workspace" }));
    await user.click(screen.getByRole("button", { name: "Create workspace" }));

    expect(create).not.toHaveBeenCalled();
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Give the workspace a name.");
    expect(alert).toHaveTextContent("Say which shared work this workspace supports.");
    expect(alert).toHaveTextContent("Connect at least one system.");
  });

  it("points to agent setup as the next step", async () => {
    const user = userEvent.setup();
    render(<CreateWorkspaceModal systems={systems} create={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "New workspace" }));
    expect(
      screen.getByText(/Next, create an Agent that uses this shared Workspace/),
    ).toBeInTheDocument();
  });
});

describe("WorkspaceCards", () => {
  it("renders the connects and used-by facts", () => {
    render(
      <WorkspaceCards
        workspaces={[
          {
            id: "workspace-compliance",
            name: "Compliance workspace",
            purpose: "Access reviews run here.",
            systemCount: 4,
            agentCount: 2,
          },
          {
            id: "workspace-vendor",
            name: "Vendor workspace",
            purpose: "Vendor work runs here.",
            systemCount: 3,
            agentCount: 1,
          },
        ]}
      />,
    );
    expect(screen.getByText("Connects 4 systems · used by 2 agents")).toBeInTheDocument();
    expect(screen.getByText("Connects 3 systems · used by 1 agent")).toBeInTheDocument();
  });
});

describe("WorkspaceDetail", () => {
  it("renders systems with grant labels and stand-in notes", () => {
    render(
      <WorkspaceDetail
        workspace={fixtureWorkspaces[0]!}
        agents={[{ id: "agent-1", name: "Production operations" }]}
      />,
    );
    expect(screen.getAllByText("View only")).toHaveLength(2);
    expect(screen.getAllByText("Can update")).toHaveLength(2);
    expect(
      screen.getByText("Stand-in for Messaging: messages are held in the outbox instead of being sent."),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Production operations/ })).toHaveAttribute(
      "href",
      "/app/agents/agent-1",
    );
    expect(screen.getByText("v2")).toBeInTheDocument();
  });
});
