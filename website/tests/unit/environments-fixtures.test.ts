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
import { agentRuntimeToolIds } from "@/lib/agents/capabilities";
import { fixtureWorkspaces, systemCatalog } from "@/lib/fixtures/environments";
import { routines } from "@/lib/fixtures/world";
import { grantsFromChoices, toolIdsForRoutine } from "@/lib/workspaces/system-catalog";

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

  it("adds rehearsal metadata to a side-effecting write grant automatically", () => {
    const grants = grantsFromChoices([
      { systemId: "crm", scope: "write", useStandIn: false },
    ]);
    expect(grants[0]?.standIn?.note).toBeTruthy();
  });

  it("keeps read grants on side-effecting systems stand-in free", () => {
    const grants = grantsFromChoices([{ systemId: "crm", scope: "read", useStandIn: false }]);
    expect(grants[0]?.standIn).toBeUndefined();
  });

  it("pins custom connection tools onto the stored grant", () => {
    const grants = grantsFromChoices(
      [{ systemId: "custom:ticketing", scope: "write", useStandIn: true }],
      [
        {
          id: "custom:ticketing",
          displayName: "Ticketing",
          sideEffecting: true,
          standInNote: "Recorded intents only.",
          tools: [
            { name: "tickets.search", sideEffecting: false },
            { name: "tickets.create", sideEffecting: true },
          ],
        },
      ],
    );
    expect(grants[0]?.tools).toEqual([
      { name: "tickets.search", sideEffecting: false },
      { name: "tickets.create", sideEffecting: true },
    ]);
  });
});

describe("toolIdsForRoutine", () => {
  it("uses only required systems and respects read versus write scope", () => {
    const grants = grantsFromChoices([
      { systemId: "document_store", scope: "read", useStandIn: false },
      { systemId: "messaging", scope: "write", useStandIn: true },
    ]);
    expect(toolIdsForRoutine(["document_store", "messaging"], grants)).toEqual([
      "google.docs_read",
      "google.drive_list_files",
      "google.sheets_read_range",
      "slack.list_channels",
      "slack.post_message",
      "slack.read_messages",
    ]);
    expect(toolIdsForRoutine(["document_store"], grants)).not.toContain(
      "google.sheets_append_row",
    );
    expect(toolIdsForRoutine(["document_store"], grants)).not.toContain(
      "google.docs_update",
    );
  });

  it("carries custom tool grants into a run", () => {
    const grants = grantsFromChoices(
      [{ systemId: "custom:ticketing", scope: "read", useStandIn: false }],
      [
        {
          id: "custom:ticketing",
          displayName: "Ticketing",
          sideEffecting: true,
          standInNote: "Recorded intents only.",
          tools: [
            { name: "tickets.search", sideEffecting: false },
            { name: "tickets.create", sideEffecting: true },
          ],
        },
      ],
    );
    expect(toolIdsForRoutine(["custom:ticketing"], grants)).toEqual(["tickets.search"]);
  });
});

describe("agentRuntimeToolIds", () => {
  it("grants compute and a domain-scoped browser from the selected agent", () => {
    expect(
      agentRuntimeToolIds({
        sandboxTemplate: "convoy-devbox-python",
        browserPolicy: { allowedDomains: ["app.example.com"], persistProfile: true },
      }),
    ).toEqual(["sandbox_exec", "sandbox_browser"]);
  });

  it("does not invent browser or compute tools absent from the environment", () => {
    expect(
      agentRuntimeToolIds({
        sandboxTemplate: "convoy-devbox-python",
        browserPolicy: null,
      }),
    ).toEqual(["sandbox_exec"]);
    expect(agentRuntimeToolIds(null)).toEqual([]);
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
