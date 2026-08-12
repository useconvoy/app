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
  {
    id: "document_store",
    displayName: "Document store",
    sideEffecting: false,
    standInNote: null,
    tools: [
      { name: "google.drive_list_files", sideEffecting: false },
      { name: "google.sheets_append_row", sideEffecting: true },
    ],
  },
  {
    id: "messaging",
    displayName: "Messaging",
    sideEffecting: true,
    standInNote: "Stand-in for Slack: messages are held in the outbox instead of being sent.",
    tools: [
      { name: "slack.read_messages", sideEffecting: false },
      { name: "slack.post_message", sideEffecting: true },
    ],
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
  it("derives Can update from enabling an action that makes changes, and isolates it", async () => {
    const create = vi.fn().mockResolvedValue({ id: "workspace-x" });
    const { user, dialog } = await openModalAndFill(create);

    await user.click(within(dialog).getByLabelText("Messaging"));
    // Safe actions are pre-enabled; turning on the mutating action
    // upgrades the grant and shows the isolation note.
    await user.click(within(dialog).getByLabelText(/post_message/));
    expect(
      within(dialog).getByText(/Rehearsal runs isolate writes automatically/),
    ).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Create workspace" }));

    expect(create).toHaveBeenCalledWith({
      name: "Finance workspace",
      purpose: "Invoicing checks run here.",
      systems: [
        {
          systemId: "messaging",
          scope: "write",
          useStandIn: true,
          tools: ["slack.read_messages", "slack.post_message"],
        },
      ],
    });
    expect(navigation.refresh).toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("keeps the default selection read-only, without a stand-in", async () => {
    const create = vi.fn().mockResolvedValue({ id: "workspace-x" });
    const { user, dialog } = await openModalAndFill(create);

    await user.click(within(dialog).getByLabelText("Messaging"));
    await user.click(within(dialog).getByRole("button", { name: "Create workspace" }));

    expect(create).toHaveBeenCalledWith({
      name: "Finance workspace",
      purpose: "Invoicing checks run here.",
      systems: [
        {
          systemId: "messaging",
          scope: "read",
          useStandIn: false,
          tools: ["slack.read_messages"],
        },
      ],
    });
  });

  it("requires at least one enabled action on a selected connector", async () => {
    const create = vi.fn();
    const { user, dialog } = await openModalAndFill(create);

    await user.click(within(dialog).getByLabelText("Messaging"));
    await user.click(within(dialog).getByLabelText(/read_messages/));
    await user.click(within(dialog).getByRole("button", { name: "Create workspace" }));

    expect(create).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Enable at least one action for Messaging.",
    );
  });

  it("filters connectors through the search box", async () => {
    const user = userEvent.setup();
    render(<CreateWorkspaceModal systems={systems} create={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "New workspace" }));
    const dialog = screen.getByRole("dialog");

    await user.type(within(dialog).getByLabelText("Search connectors"), "docu");
    expect(within(dialog).getByLabelText(/Document store/)).toBeInTheDocument();
    expect(within(dialog).queryByLabelText(/Messaging/)).not.toBeInTheDocument();

    await user.clear(within(dialog).getByLabelText("Search connectors"));
    await user.type(within(dialog).getByLabelText("Search connectors"), "zzz");
    expect(within(dialog).getByText("No connector matches that search.")).toBeInTheDocument();
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
    expect(alert).toHaveTextContent("Pick at least one connector.");
  });

  it("only lets healthy connectors be selected, pointing the rest at the Connectors page", async () => {
    const user = userEvent.setup();
    render(
      <CreateWorkspaceModal
        systems={[
          { ...systems[0]!, status: "connected" },
          { ...systems[1]!, status: "not_connected" },
        ]}
        create={vi.fn()}
      />,
    );
    await user.click(screen.getByRole("button", { name: "New workspace" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByLabelText(/Document store/)).toBeEnabled();
    expect(within(dialog).getByLabelText(/Messaging/)).toBeDisabled();
    expect(within(dialog).getByRole("link", { name: "Connect it first" })).toHaveAttribute(
      "href",
      "/app/connectors",
    );
  });

  it("explains where to connect services when nothing is connected yet", async () => {
    const user = userEvent.setup();
    render(<CreateWorkspaceModal systems={[]} create={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "New workspace" }));
    expect(screen.getByText(/Nothing is connected yet/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Connectors page/ })).toHaveAttribute(
      "href",
      "/app/connectors",
    );
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
      screen.getByText("Stand-in for Slack: messages are held in the outbox instead of being sent."),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Production operations/ })).toHaveAttribute(
      "href",
      "/app/agents/agent-1",
    );
    expect(screen.getByText("v2")).toBeInTheDocument();
  });
});
