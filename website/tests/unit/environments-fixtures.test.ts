/**
 * Shape stability for the environments fixture adapter (W4 gate): the
 * workspaces the console renders must bind to the local stub registry's
 * runnable environment ids, side-effecting write grants must carry their
 * stand-ins, and the workspace-to-routine usage mapping must stay put.
 */
import { beforeEach, describe, expect, it } from "vitest";

import {
  environmentsClient,
  routinesUsingWorkspace,
  workspaceForRoutine,
  type Workspace,
} from "@/lib/api/environments";
import { fixtureWorkspaces, systemCatalog } from "@/lib/fixtures/environments";
import { routines } from "@/lib/fixtures/world";

const ORG = "org-test";
const RUNNABLE_REHEARSAL_IDS = ["stub-local", "stub-local-virtual"];

beforeEach(() => {
  globalThis.__convoyWorkspaceStore = undefined;
});

describe("fixture workspaces", () => {
  it("expose runnable production and rehearsal environment ids", async () => {
    const workspaces = await environmentsClient().listWorkspaces(ORG);
    expect(workspaces.length).toBeGreaterThanOrEqual(2);
    for (const workspace of workspaces) {
      expect(workspace.environmentId).toBe("prod-local");
      expect(RUNNABLE_REHEARSAL_IDS).toContain(workspace.rehearsalEnvironmentId);
    }
  });

  it("carry a stand-in on every side-effecting write grant", async () => {
    const workspaces = await environmentsClient().listWorkspaces(ORG);
    for (const workspace of workspaces) {
      for (const grant of workspace.systems) {
        if (grant.sideEffecting && grant.scope === "write") {
          expect(grant.standIn?.note).toBeTruthy();
        }
        if (grant.standIn) {
          expect(grant.sideEffecting).toBe(true);
        }
      }
    }
  });

  it("connect the demo world: compliance covers the review, vendor adds the CRM", () => {
    const [compliance, vendor] = fixtureWorkspaces as [Workspace, Workspace];
    expect(compliance.systems.map((grant) => grant.systemId)).toEqual([
      "identity_provider",
      "hris",
      "document_store",
      "messaging",
    ]);
    expect(vendor.systems.map((grant) => grant.systemId)).toContain("crm");
  });
});

describe("workspace to routine usage mapping", () => {
  it("maps every fixture routine to a covering workspace", () => {
    for (const routine of routines) {
      const workspace = workspaceForRoutine(routine, fixtureWorkspaces);
      expect(workspace).not.toBeNull();
      const connected = new Set(workspace?.systems.map((grant) => grant.systemId));
      for (const systemId of routine.systems) {
        expect(connected.has(systemId)).toBe(true);
      }
    }
  });

  it("counts usage per workspace without double-counting a routine", () => {
    const usage = fixtureWorkspaces.map(
      (workspace) => routinesUsingWorkspace(workspace, fixtureWorkspaces, routines).length,
    );
    expect(usage.reduce((sum, count) => sum + count, 0)).toBe(routines.length);
    const compliance = routinesUsingWorkspace(fixtureWorkspaces[0]!, fixtureWorkspaces, routines);
    expect(compliance.map((routine) => routine.id)).toEqual([
      "routine-access-review",
      "routine-attestation-chase",
    ]);
    const vendor = routinesUsingWorkspace(fixtureWorkspaces[1]!, fixtureWorkspaces, routines);
    expect(vendor.map((routine) => routine.id)).toEqual(["routine-vendor-check"]);
  });
});

describe("workspace creation", () => {
  it("persists an org-created workspace in the adapter store", async () => {
    const client = environmentsClient();
    const created = await client.createWorkspace(ORG, {
      name: "Finance workspace",
      purpose: "Invoicing checks run here.",
      systems: [
        { systemId: "document_store", scope: "write", useStandIn: false },
        { systemId: "messaging", scope: "write", useStandIn: true },
      ],
    });
    expect(created.rehearsalEnvironmentId).toBe("stub-local");
    const fetched = await client.getWorkspace(ORG, created.id);
    expect(fetched?.name).toBe("Finance workspace");
    const messaging = fetched?.systems.find((grant) => grant.systemId === "messaging");
    expect(messaging?.standIn?.note).toBeTruthy();
    // Other orgs never see it.
    expect(await client.getWorkspace("org-other", created.id)).toBeNull();
  });

  it("refuses a side-effecting write grant without its stand-in", async () => {
    await expect(
      environmentsClient().createWorkspace(ORG, {
        name: "Careless workspace",
        purpose: "Should not exist.",
        systems: [{ systemId: "crm", scope: "write", useStandIn: false }],
      }),
    ).rejects.toThrow(/stand-in/);
  });

  it("keeps read grants on side-effecting systems stand-in free", async () => {
    const created = await environmentsClient().createWorkspace(ORG, {
      name: "Read only workspace",
      purpose: "Lookups only.",
      systems: [{ systemId: "crm", scope: "read", useStandIn: false }],
    });
    expect(created.systems[0]?.standIn).toBeUndefined();
  });
});

describe("system catalog", () => {
  it("gives every side-effecting system a plain stand-in note", () => {
    for (const system of systemCatalog) {
      if (system.sideEffecting) {
        expect(system.standInNote).toMatch(/^Stand-in for /);
      } else {
        expect(system.standInNote).toBeNull();
      }
    }
  });
});
