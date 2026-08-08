/**
 * Shape stability for the workspace fixtures and the pure workspace
 * helpers: the fixture workspaces must bind to the local stub registry's
 * runnable environment ids, side-effecting write grants must carry their
 * stand-ins (both in the fixtures and through grantsFromChoices, which
 * production workspace creation rides), and the workspace-to-routine
 * usage mapping must stay put.
 */
import { describe, expect, it } from "vitest";

import {
  routinesUsingWorkspace,
  workspaceForRoutine,
  type Workspace,
} from "@/lib/api/environments";
import { fixtureWorkspaces, systemCatalog } from "@/lib/fixtures/environments";
import { routines } from "@/lib/fixtures/world";
import { grantsFromChoices } from "@/lib/workspaces/system-catalog";

const RUNNABLE_REHEARSAL_IDS = ["stub-local", "stub-local-virtual"];

describe("fixture workspaces", () => {
  it("expose runnable production and rehearsal environment ids", () => {
    expect(fixtureWorkspaces.length).toBeGreaterThanOrEqual(2);
    for (const workspace of fixtureWorkspaces) {
      expect(workspace.environmentId).toBe("prod-local");
      expect(RUNNABLE_REHEARSAL_IDS).toContain(workspace.rehearsalEnvironmentId);
    }
  });

  it("carry a stand-in on every side-effecting write grant", () => {
    for (const workspace of fixtureWorkspaces) {
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

  it("prefers a recorded workspace binding over system coverage", () => {
    const [compliance, vendor] = fixtureWorkspaces as [Workspace, Workspace];
    const bound = [{ systems: ["document_store"], workspaceId: vendor.id }];
    // Coverage alone would pick the compliance workspace (it comes first);
    // the recorded binding must win.
    expect(routinesUsingWorkspace(compliance, fixtureWorkspaces, bound)).toHaveLength(0);
    expect(routinesUsingWorkspace(vendor, fixtureWorkspaces, bound)).toHaveLength(1);
  });
});

describe("grantsFromChoices", () => {
  it("builds grants with stand-ins riding side-effecting writes", () => {
    const grants = grantsFromChoices([
      { systemId: "document_store", scope: "write", useStandIn: false },
      { systemId: "messaging", scope: "write", useStandIn: true },
    ]);
    expect(grants).toHaveLength(2);
    const messaging = grants.find((grant) => grant.systemId === "messaging");
    expect(messaging?.standIn?.note).toBeTruthy();
    const documents = grants.find((grant) => grant.systemId === "document_store");
    expect(documents?.standIn).toBeUndefined();
  });

  it("refuses a side-effecting write grant without its stand-in", () => {
    expect(() =>
      grantsFromChoices([{ systemId: "crm", scope: "write", useStandIn: false }]),
    ).toThrow(/stand-in/);
  });

  it("keeps read grants on side-effecting systems stand-in free", () => {
    const grants = grantsFromChoices([{ systemId: "crm", scope: "read", useStandIn: false }]);
    expect(grants[0]?.standIn).toBeUndefined();
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
