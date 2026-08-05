/**
 * The typed client interface for the environments service (registry of
 * workspaces, their system grants, and their rehearsal bindings). The
 * service is not built yet, so the only implementation is a fixture
 * adapter over `lib/fixtures/environments` plus an in-process store for
 * org-created workspaces, mirroring the run directory's seam.
 *
 * TODO(environments-E0): replace the fixture adapter with real calls to
 * the environments directory behind the single authenticated edge and
 * delete the in-process store.
 */
import "server-only";

import { randomUUID } from "node:crypto";

import type { FixtureRoutine } from "@/lib/fixtures/world";
import { catalogSystem, fixtureWorkspaces } from "@/lib/fixtures/environments";

export interface StandIn {
  /** Plain-language note shown wherever the stand-in appears. */
  note: string;
}

export interface SystemGrant {
  systemId: string;
  displayName: string;
  scope: "read" | "write";
  /** True when a write through this system touches the outside world. */
  sideEffecting: boolean;
  /** Required whenever a side-effecting system is granted write. */
  standIn?: StandIn;
}

export interface WorkspaceVersion {
  version: number;
  note: string;
  createdAt: string;
}

export interface Workspace {
  id: string;
  name: string;
  purpose: string;
  /** Runnable production binding in the environments registry. */
  environmentId: string;
  /** Runnable rehearsal-copy binding; every workspace carries one. */
  rehearsalEnvironmentId: string;
  systems: SystemGrant[];
  clockMode: "wall" | "virtual";
  versions: WorkspaceVersion[];
  createdAt: string;
}

/**
 * Shape of the compatibility report rendered during catalog installs:
 * green checks, mapping choices, and connect prompts.
 * TODO(environments-E0): the environments service will own this
 * computation; lib/catalog/compat computes it website-side until then.
 */
export interface CompatibilityReport {
  workspaceId: string;
  greenChecks: string[];
  mappingChoices: Array<{ systemId: string; options: string[] }>;
  connectPrompts: string[];
  vendorSpecificToolCount: number;
}

export interface CreateWorkspaceInput {
  name: string;
  purpose: string;
  systems: Array<{ systemId: string; scope: "read" | "write"; useStandIn: boolean }>;
}

export interface EnvironmentsClient {
  listWorkspaces(orgId: string): Promise<Workspace[]>;
  getWorkspace(orgId: string, workspaceId: string): Promise<Workspace | null>;
  createWorkspace(orgId: string, input: CreateWorkspaceInput): Promise<Workspace>;
}

declare global {
  var __convoyWorkspaceStore: Map<string, Workspace[]> | undefined;
}

/** Org-created workspaces, kept in process until the registry exists. */
function createdStore(): Map<string, Workspace[]> {
  if (!globalThis.__convoyWorkspaceStore) {
    globalThis.__convoyWorkspaceStore = new Map();
  }
  return globalThis.__convoyWorkspaceStore;
}

class FixtureEnvironmentsClient implements EnvironmentsClient {
  async listWorkspaces(orgId: string): Promise<Workspace[]> {
    return [...fixtureWorkspaces, ...(createdStore().get(orgId) ?? [])];
  }

  async getWorkspace(orgId: string, workspaceId: string): Promise<Workspace | null> {
    const all = await this.listWorkspaces(orgId);
    return all.find((workspace) => workspace.id === workspaceId) ?? null;
  }

  async createWorkspace(orgId: string, input: CreateWorkspaceInput): Promise<Workspace> {
    const systems = input.systems.map((choice) => {
      const system = catalogSystem(choice.systemId);
      if (!system) throw new Error(`unknown system: ${choice.systemId}`);
      const needsStandIn = system.sideEffecting && choice.scope === "write";
      if (needsStandIn && !choice.useStandIn) {
        // The create modal blocks this client-side; the adapter holds the
        // same line so no caller can slip a live side-effecting grant in.
        throw new Error(`a stand-in is required for ${system.displayName}`);
      }
      const grant: SystemGrant = {
        systemId: system.id,
        displayName: system.displayName,
        scope: choice.scope,
        sideEffecting: system.sideEffecting,
      };
      if (needsStandIn && system.standInNote) {
        grant.standIn = { note: system.standInNote };
      }
      return grant;
    });
    const now = new Date().toISOString();
    const workspace: Workspace = {
      id: `workspace-${randomUUID()}`,
      name: input.name,
      purpose: input.purpose,
      // Created workspaces bind to the local stub registry so their runs
      // execute for real. TODO(environments-E0): real bindings.
      environmentId: "prod-local",
      rehearsalEnvironmentId: "stub-local",
      systems,
      clockMode: "wall",
      versions: [{ version: 1, note: "Created", createdAt: now }],
      createdAt: now,
    };
    const existing = createdStore().get(orgId) ?? [];
    createdStore().set(orgId, [...existing, workspace]);
    return workspace;
  }
}

/** The one environments client. TODO(environments-E0): real adapter. */
export function environmentsClient(): EnvironmentsClient {
  return new FixtureEnvironmentsClient();
}

/**
 * Which workspace a routine runs in. Until the registry records real
 * bindings, the first workspace whose grants cover every system the
 * routine needs wins; workspace order is the fixture/creation order.
 * TODO(environments-E0): replace with recorded routine-to-workspace bindings.
 */
export function workspaceForRoutine(
  routine: Pick<FixtureRoutine, "systems">,
  workspaces: Workspace[],
): Workspace | null {
  return (
    workspaces.find((workspace) => {
      const connected = new Set(workspace.systems.map((grant) => grant.systemId));
      return routine.systems.every((systemId) => connected.has(systemId));
    }) ?? null
  );
}

/** The inverse mapping, for "used by M routines" workspace cards. */
export function routinesUsingWorkspace(
  workspace: Workspace,
  workspaces: Workspace[],
  routines: ReadonlyArray<FixtureRoutine>,
): FixtureRoutine[] {
  return routines.filter((routine) => workspaceForRoutine(routine, workspaces)?.id === workspace.id);
}
