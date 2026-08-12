import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

const navigation = vi.hoisted(() => ({ refresh: vi.fn(), push: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: navigation.refresh, push: navigation.push }),
}));

import {
  ConnectorsBoard,
  type ConnectorCard,
  type CustomConnectorCard,
} from "@/app/(portal)/app/connectors/ConnectorsBoard";

const connectors: ConnectorCard[] = [
  {
    systemId: "messaging",
    displayName: "Slack",
    provider: "slack",
    connectionId: "conn-slack",
    state: "connected",
    toolCount: 3,
    sideEffecting: true,
  },
  {
    systemId: "document_store",
    displayName: "Google Drive",
    provider: "google",
    connectionId: null,
    state: "not_connected",
    toolCount: 3,
    sideEffecting: false,
  },
  {
    systemId: "code_host",
    displayName: "GitHub",
    provider: "github",
    connectionId: "conn-github",
    state: "needs_reauth",
    toolCount: 3,
    sideEffecting: true,
  },
];

const custom: CustomConnectorCard[] = [
  {
    connectionId: "conn-tools",
    displayName: "Internal CRM",
    state: "connected",
    toolCount: 2,
    tools: ["crm.lookup", "crm.update"],
  },
];

function renderBoard(overrides: Partial<Parameters<typeof ConnectorsBoard>[0]> = {}) {
  return render(
    <ConnectorsBoard
      registryLinked
      connectors={connectors}
      customConnectors={custom}
      connect={vi.fn()}
      addCustom={vi.fn()}
      reattach={vi.fn()}
      checkHealth={vi.fn().mockResolvedValue({ ok: true, message: "Healthy." })}
      {...overrides}
    />,
  );
}

describe("ConnectorsBoard", () => {
  it("splits the inventory into Connected and Available", () => {
    renderBoard();
    const connected = screen.getByRole("heading", { name: "Connected" }).closest("section")!;
    expect(within(connected).getByText("Slack")).toBeInTheDocument();
    expect(within(connected).getByText("GitHub")).toBeInTheDocument();
    expect(within(connected).getByText("Internal CRM")).toBeInTheDocument();
    const available = screen.getByRole("heading", { name: "Available" }).closest("section")!;
    expect(within(available).getByText("Google Drive")).toBeInTheDocument();
  });

  it("offers Reconnect on a connection that needs re-auth", () => {
    renderBoard();
    expect(screen.getByRole("button", { name: "Reconnect" })).toBeInTheDocument();
  });

  it("filters everything through the search box", async () => {
    const user = userEvent.setup();
    renderBoard();
    await user.type(screen.getByLabelText("Search connectors"), "git");
    expect(screen.getByText("GitHub")).toBeInTheDocument();
    expect(screen.queryByText("Slack")).not.toBeInTheDocument();
    expect(screen.queryByText("Google Drive")).not.toBeInTheDocument();

    await user.clear(screen.getByLabelText("Search connectors"));
    await user.type(screen.getByLabelText("Search connectors"), "zzz");
    expect(screen.getByText("No connector matches that search.")).toBeInTheDocument();
  });

  it("runs the health probe and reports the verdict", async () => {
    const checkHealth = vi
      .fn()
      .mockResolvedValue({ ok: false, message: "The provider rejected the credential. Reconnect it below." });
    const user = userEvent.setup();
    renderBoard({ checkHealth });
    await user.click(screen.getAllByRole("button", { name: "Check health" })[0]!);
    await waitFor(() =>
      expect(
        screen.getByText("The provider rejected the credential. Reconnect it below."),
      ).toBeInTheDocument(),
    );
    expect(checkHealth).toHaveBeenCalledWith("conn-slack");
  });
});
