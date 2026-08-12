/**
 * The website <-> environments registry mapping seam: role translation,
 * grant-scope-to-allowlist translation, which granted systems become
 * registry connections at all, and the merge of a registry workspace row
 * with its website cache row. Pure functions only; the adapter's HTTP and
 * DB paths stay out of unit scope.
 */
import { describe, expect, it } from "vitest";

import type { SystemGrant, Workspace } from "@/lib/api/environments";
import {
  connectionPlanForGrants,
  declaredManifest,
  environmentsRole,
  normalizeClockMode,
  toolAllowlistFor,
  withCatalogFacts,
  workspaceFromRegistryRow,
  type RegistryWorkspaceRow,
} from "@/lib/api/environments-mapping";
import { ROLES } from "@/lib/orgs/validation";
import { grantsFromChoices, systemCatalog } from "@/lib/workspaces/system-catalog";

describe("environmentsRole", () => {
  it("maps admin to admin, operator to builder, everyone else to member", () => {
    expect(environmentsRole("admin")).toBe("admin");
    expect(environmentsRole("operator")).toBe("builder");
    expect(environmentsRole("member")).toBe("member");
    expect(environmentsRole("viewer")).toBe("member");
  });

  it("covers the whole website role vocabulary", () => {
    for (const role of ROLES) {
      expect(["admin", "builder", "member"]).toContain(environmentsRole(role));
    }
  });
});

describe("catalog connection specs", () => {
  it("give every mapped system at least one inline read tool", () => {
    // A read-scoped grant must never translate to an empty allowlist.
    for (const system of systemCatalog) {
      if (!system.connection) continue;
      expect(toolAllowlistFor(system.connection, "read").length).toBeGreaterThan(0);
    }
  });

  it("keep inline tools free of declared side effects", () => {
    for (const system of systemCatalog) {
      for (const tool of system.connection?.tools ?? []) {
        if (tool.execution === "inline") expect(tool.sideEffecting).toBe(false);
      }
    }
  });
});

describe("toolAllowlistFor", () => {
  const documentStore = systemCatalog.find((system) => system.id === "document_store")!;

  it("read scope allowlists only the inline read tools", () => {
    expect(toolAllowlistFor(documentStore.connection!, "read")).toEqual([
      "google.drive_list_files",
      "google.sheets_read_range",
    ]);
  });

  it("write scope allowlists the whole declared surface", () => {
    expect(toolAllowlistFor(documentStore.connection!, "write")).toEqual([
      "google.drive_list_files",
      "google.sheets_read_range",
      "google.sheets_append_row",
    ]);
  });
});

describe("connectionPlanForGrants", () => {
  const grants = grantsFromChoices([
    { systemId: "document_store", scope: "write", useStandIn: false },
    { systemId: "messaging", scope: "read", useStandIn: false },
    { systemId: "identity_provider", scope: "read", useStandIn: false },
    { systemId: "crm", scope: "write", useStandIn: true },
  ]);
  const plans = connectionPlanForGrants(grants);

  it("maps only systems the registry has a provider for", () => {
    expect(plans.map((plan) => plan.provider)).toEqual(["google", "slack"]);
    expect(plans.map((plan) => plan.systemId)).toEqual(["document_store", "messaging"]);
  });

  it("translates each grant's scope into its allowlist", () => {
    const google = plans.find((plan) => plan.provider === "google")!;
    expect(google.toolAllowlist).toContain("google.sheets_append_row");
    const slack = plans.find((plan) => plan.provider === "slack")!;
    expect(slack.toolAllowlist).toEqual(["slack.list_channels", "slack.read_messages"]);
  });

  it("declares manifests whose mutating tools are promoted and flagged", () => {
    const slack = plans.find((plan) => plan.provider === "slack")!;
    const post = slack.manifest.tools.find((tool) => tool.name === "slack.post_message")!;
    expect(post.execution).toBe("promoted");
    expect(post.sideEffecting).toBe(true);
  });

  it("declaredManifest carries exactly name, execution, and sideEffecting", () => {
    const messaging = systemCatalog.find((system) => system.id === "messaging")!;
    for (const tool of declaredManifest(messaging.connection!).tools) {
      expect(Object.keys(tool).sort()).toEqual(["execution", "name", "sideEffecting"]);
    }
  });
});

describe("normalizeClockMode", () => {
  it("passes virtual through and defaults everything else to wall", () => {
    expect(normalizeClockMode("virtual")).toBe("virtual");
    expect(normalizeClockMode("wall")).toBe("wall");
    expect(normalizeClockMode("lunar")).toBe("wall");
    expect(normalizeClockMode(undefined)).toBe("wall");
    expect(normalizeClockMode(null)).toBe("wall");
  });
});

describe("withCatalogFacts", () => {
  it("re-derives displayName and sideEffecting from the catalog", () => {
    const stale: SystemGrant[] = [
      { systemId: "messaging", displayName: "Msgs", scope: "read", sideEffecting: false },
    ];
    const [fresh] = withCatalogFacts(stale);
    expect(fresh).toMatchObject({ displayName: "Slack", sideEffecting: true });
  });

  it("leaves grants for unknown systems untouched", () => {
    const foreign: SystemGrant[] = [
      { systemId: "mystery", displayName: "Mystery", scope: "write", sideEffecting: true },
    ];
    expect(withCatalogFacts(foreign)).toEqual(foreign);
  });
});

describe("workspaceFromRegistryRow", () => {
  const row: RegistryWorkspaceRow = {
    workspaceId: "ws_1234567890abcdef1234",
    version: 3,
    name: "Compliance",
    purpose: "Access reviews run here.",
    policyHash: "ph_abc",
    environmentId: "env_prod",
    rehearsalEnvironmentId: "env_rehearsal",
    clockMode: "virtual",
    rehearsalClockMode: "virtual",
  };
  const cache = {
    id: "6f1f19a2-13aa-4bbb-8ccc-9ddddeeeefff",
    systems: grantsFromChoices([{ systemId: "document_store", scope: "read", useStandIn: false }]),
    versions: [{ version: 3, note: "Created", createdAt: "2026-08-01T00:00:00.000Z" }],
    createdAt: "2026-08-01T00:00:00.000Z",
  };

  it("takes bindings and clock mode straight from the registry row", () => {
    const workspace: Workspace = workspaceFromRegistryRow(row, cache);
    expect(workspace.environmentId).toBe("env_prod");
    expect(workspace.rehearsalEnvironmentId).toBe("env_rehearsal");
    expect(workspace.clockMode).toBe("virtual");
    expect(workspace.name).toBe("Compliance");
    expect(workspace.purpose).toBe("Access reviews run here.");
  });

  it("keeps the website id, grants, and version notes from the cache", () => {
    const workspace = workspaceFromRegistryRow(row, cache);
    expect(workspace.id).toBe(cache.id);
    expect(workspace.systems.map((grant) => grant.systemId)).toEqual(["document_store"]);
    expect(workspace.versions).toEqual(cache.versions);
    expect(workspace.createdAt).toBe(cache.createdAt);
  });
});
