/**
 * The pure compatibility computation (DESIGN §6): capability requirements
 * crossed with a workspace's system grants produce the report the install
 * flow renders. Table-driven over the matching rules: family match with
 * scope covering, one candidate green, several a mapping choice, none a
 * connect prompt, and vendor-specific tools counted only when their
 * vendor is not connected.
 */
import { describe, expect, it } from "vitest";

import type { SystemGrant } from "@/lib/api/environments";
import {
  computeCompatibility,
  parseVersionRef,
  reportState,
  systemFamily,
  versionRef,
  type CapabilityRequirements,
} from "@/lib/catalog/compat";

function grant(systemId: string, scope: "read" | "write"): SystemGrant {
  return { systemId, displayName: systemId, scope, sideEffecting: false };
}

function requirements(
  systems: Array<{ systemId: string; scope: "read" | "write" }>,
  vendorSpecificTools: Array<{ tool: string; vendorSystemId: string }> = [],
): CapabilityRequirements {
  return { systems, vendorSpecificTools };
}

interface Case {
  name: string;
  requirements: CapabilityRequirements;
  grants: SystemGrant[];
  expected: {
    greenChecks: string[];
    mappingChoices: Array<{ systemId: string; options: string[] }>;
    connectPrompts: string[];
    vendorSpecificToolCount: number;
    state: "green" | "needs_mapping" | "needs_connect";
  };
}

const cases: Case[] = [
  {
    name: "exact match on every requirement is all green",
    requirements: requirements([
      { systemId: "identity_provider", scope: "read" },
      { systemId: "messaging", scope: "write" },
    ]),
    grants: [grant("identity_provider", "read"), grant("messaging", "write")],
    expected: {
      greenChecks: ["identity_provider", "messaging"],
      mappingChoices: [],
      connectPrompts: [],
      vendorSpecificToolCount: 0,
      state: "green",
    },
  },
  {
    name: "a read requirement is satisfied by a write grant",
    requirements: requirements([{ systemId: "document_store", scope: "read" }]),
    grants: [grant("document_store", "write")],
    expected: {
      greenChecks: ["document_store"],
      mappingChoices: [],
      connectPrompts: [],
      vendorSpecificToolCount: 0,
      state: "green",
    },
  },
  {
    name: "a write requirement against a read-only grant prompts to connect",
    requirements: requirements([{ systemId: "messaging", scope: "write" }]),
    grants: [grant("messaging", "read")],
    expected: {
      greenChecks: [],
      mappingChoices: [],
      connectPrompts: ["messaging"],
      vendorSpecificToolCount: 0,
      state: "needs_connect",
    },
  },
  {
    name: "a missing system prompts to connect",
    requirements: requirements([{ systemId: "crm", scope: "write" }]),
    grants: [grant("document_store", "write")],
    expected: {
      greenChecks: [],
      mappingChoices: [],
      connectPrompts: ["crm"],
      vendorSpecificToolCount: 0,
      state: "needs_connect",
    },
  },
  {
    name: "a vendor-flavored grant satisfies its capability family",
    requirements: requirements([{ systemId: "crm", scope: "write" }]),
    grants: [grant("crm:hubspot", "write")],
    expected: {
      greenChecks: ["crm"],
      mappingChoices: [],
      connectPrompts: [],
      vendorSpecificToolCount: 0,
      state: "green",
    },
  },
  {
    name: "two satisfying vendors need a mapping choice",
    requirements: requirements([{ systemId: "crm", scope: "write" }]),
    grants: [grant("crm:salesforce", "write"), grant("crm:hubspot", "write")],
    expected: {
      greenChecks: [],
      mappingChoices: [{ systemId: "crm", options: ["crm:salesforce", "crm:hubspot"] }],
      connectPrompts: [],
      vendorSpecificToolCount: 0,
      state: "needs_mapping",
    },
  },
  {
    name: "only scope-covering candidates count toward the mapping",
    requirements: requirements([{ systemId: "crm", scope: "write" }]),
    grants: [grant("crm:salesforce", "read"), grant("crm:hubspot", "write")],
    expected: {
      greenChecks: ["crm"],
      mappingChoices: [],
      connectPrompts: [],
      vendorSpecificToolCount: 0,
      state: "green",
    },
  },
  {
    name: "vendor-specific tools count only when their vendor is not connected",
    requirements: requirements(
      [{ systemId: "crm", scope: "write" }],
      [
        { tool: "salesforce_bulk_export", vendorSystemId: "crm:salesforce" },
        { tool: "hubspot_sequences", vendorSystemId: "crm:hubspot" },
      ],
    ),
    grants: [grant("crm:hubspot", "write")],
    expected: {
      greenChecks: ["crm"],
      mappingChoices: [],
      connectPrompts: [],
      vendorSpecificToolCount: 1,
      state: "green",
    },
  },
  {
    name: "mixed report keeps every bucket independent",
    requirements: requirements(
      [
        { systemId: "identity_provider", scope: "read" },
        { systemId: "crm", scope: "write" },
        { systemId: "messaging", scope: "write" },
      ],
      [{ tool: "salesforce_bulk_export", vendorSystemId: "crm:salesforce" }],
    ),
    grants: [
      grant("identity_provider", "read"),
      grant("crm:salesforce", "write"),
      grant("crm:hubspot", "write"),
    ],
    expected: {
      greenChecks: ["identity_provider"],
      mappingChoices: [{ systemId: "crm", options: ["crm:salesforce", "crm:hubspot"] }],
      connectPrompts: ["messaging"],
      vendorSpecificToolCount: 0,
      state: "needs_connect",
    },
  },
];

describe("computeCompatibility", () => {
  it.each(cases)("$name", ({ requirements: reqs, grants, expected }) => {
    const report = computeCompatibility(reqs, { id: "workspace-1", systems: grants });
    expect(report.workspaceId).toBe("workspace-1");
    expect(report.greenChecks).toEqual(expected.greenChecks);
    expect(report.mappingChoices).toEqual(expected.mappingChoices);
    expect(report.connectPrompts).toEqual(expected.connectPrompts);
    expect(report.vendorSpecificToolCount).toBe(expected.vendorSpecificToolCount);
    expect(reportState(report)).toBe(expected.state);
  });
});

describe("version refs", () => {
  it("round-trips routine id and version", () => {
    expect(versionRef("routine-access-review", 3)).toBe("routine-access-review@v3");
    expect(parseVersionRef("routine-access-review@v3")).toEqual({
      routineId: "routine-access-review",
      version: 3,
    });
  });

  it("tolerates a ref without a version", () => {
    expect(parseVersionRef("routine-x")).toEqual({ routineId: "routine-x", version: 0 });
  });

  it("families split on the vendor separator only", () => {
    expect(systemFamily("crm")).toBe("crm");
    expect(systemFamily("crm:salesforce")).toBe("crm");
    expect(systemFamily("identity_provider")).toBe("identity_provider");
  });
});
