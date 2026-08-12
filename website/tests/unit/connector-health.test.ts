import { describe, expect, it } from "vitest";

import {
  isHealthyConnection,
  unconnectableGrants,
  type ConnectionFacts,
} from "@/lib/workspaces/connector-health";

function connection(overrides: Partial<ConnectionFacts>): ConnectionFacts {
  return {
    connectionId: "conn-1",
    kind: "mcp_managed",
    provider: "slack",
    displayName: "Slack",
    status: "active",
    hasCredential: true,
    ...overrides,
  };
}

describe("isHealthyConnection", () => {
  it("wants an active connection with a credential", () => {
    expect(isHealthyConnection(connection({}))).toBe(true);
    expect(isHealthyConnection(undefined)).toBe(false);
    expect(isHealthyConnection(connection({ status: "needs_reauth" }))).toBe(false);
    expect(isHealthyConnection(connection({ hasCredential: false }))).toBe(false);
  });

  it("lets custom connectors skip the credential, not the status", () => {
    expect(isHealthyConnection(connection({ hasCredential: false }), true)).toBe(true);
    expect(isHealthyConnection(connection({ status: "revoked", hasCredential: false }), true)).toBe(
      false,
    );
  });
});

describe("unconnectableGrants", () => {
  const healthy = [
    connection({ provider: "slack", displayName: "Slack" }),
    connection({
      connectionId: "conn-google",
      provider: "google",
      displayName: "Google Drive",
    }),
  ];

  it("passes grants backed by healthy connections", () => {
    expect(
      unconnectableGrants([{ systemId: "messaging" }, { systemId: "document_store" }], healthy),
    ).toEqual([]);
  });

  it("names the connectors that are missing or unhealthy", () => {
    expect(unconnectableGrants([{ systemId: "messaging" }], [])).toEqual(["Slack"]);
    expect(
      unconnectableGrants(
        [{ systemId: "document_store" }],
        [connection({ provider: "google", displayName: "Google Drive", hasCredential: false })],
      ),
    ).toEqual(["Google Drive"]);
  });

  it("lets catalog systems without a provider pass through", () => {
    expect(unconnectableGrants([{ systemId: "hris" }], [])).toEqual([]);
  });

  it("resolves custom grants by connection id with the credential optional", () => {
    const custom = connection({
      connectionId: "conn-tools",
      kind: "mcp_custom",
      provider: "mcp_custom",
      displayName: "Internal CRM",
      hasCredential: false,
    });
    expect(unconnectableGrants([{ systemId: "custom:conn-tools" }], [custom])).toEqual([]);
    expect(unconnectableGrants([{ systemId: "custom:conn-gone" }], [custom])).toEqual([
      "a custom connector",
    ]);
    expect(
      unconnectableGrants(
        [{ systemId: "custom:conn-tools" }],
        [connection({ ...custom, status: "revoked" })],
      ),
    ).toEqual(["Internal CRM"]);
  });
});
