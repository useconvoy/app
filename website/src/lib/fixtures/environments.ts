/**
 * Fixture data for the environments service, which is not built yet
 * (TODO(environments-E0): the directory/registry replaces all of this).
 * Shapes follow the typed client interface in `lib/api/environments`; the
 * runnable environment ids ("prod-local", "stub-local", "stub-local-virtual")
 * are the real local runtime's stub registry, so on-demand runs triggered
 * from these workspaces actually execute against `make e2e-up`.
 */

import type { SystemGrant, Workspace } from "@/lib/api/environments";

/** The system catalog the create-workspace picker offers. */
export interface FixtureSystem {
  id: string;
  displayName: string;
  /** True when granting write would touch the world outside the org. */
  sideEffecting: boolean;
  /** Plain-language stand-in note for side-effecting systems. */
  standInNote: string | null;
}

export const systemCatalog: FixtureSystem[] = [
  {
    id: "identity_provider",
    displayName: "Identity provider",
    sideEffecting: false,
    standInNote: null,
  },
  {
    id: "hris",
    displayName: "HR system",
    sideEffecting: false,
    standInNote: null,
  },
  {
    id: "document_store",
    displayName: "Document store",
    sideEffecting: false,
    standInNote: null,
  },
  {
    id: "messaging",
    displayName: "Messaging",
    sideEffecting: true,
    standInNote: "Stand-in for Messaging: messages are held in the outbox instead of being sent.",
  },
  {
    id: "crm",
    displayName: "CRM",
    sideEffecting: true,
    standInNote: "Stand-in for CRM: record changes are noted for review instead of being applied.",
  },
];

export function catalogSystem(systemId: string): FixtureSystem | undefined {
  return systemCatalog.find((system) => system.id === systemId);
}

/** Build a grant from the catalog; stand-ins ride side-effecting writes. */
function grant(systemId: string, scope: "read" | "write", withStandIn = false): SystemGrant {
  const system = catalogSystem(systemId);
  if (!system) throw new Error(`unknown fixture system: ${systemId}`);
  return {
    systemId: system.id,
    displayName: system.displayName,
    scope,
    sideEffecting: system.sideEffecting,
    ...(withStandIn && system.standInNote ? { standIn: { note: system.standInNote } } : {}),
  };
}

/**
 * The two fixture workspaces of the demo world: compliance work (access
 * review + attestation chase) and vendor work (adds the CRM).
 */
export const fixtureWorkspaces: Workspace[] = [
  {
    id: "workspace-compliance",
    name: "Compliance workspace",
    purpose: "Access reviews and policy attestations run here.",
    environmentId: "prod-local",
    rehearsalEnvironmentId: "stub-local-virtual",
    systems: [
      grant("identity_provider", "read"),
      grant("hris", "read"),
      grant("document_store", "write"),
      grant("messaging", "write", true),
    ],
    clockMode: "virtual",
    versions: [
      { version: 2, note: "Connected messaging", createdAt: "2026-07-14T09:30:00Z" },
      { version: 1, note: "Created", createdAt: "2026-06-30T15:00:00Z" },
    ],
    createdAt: "2026-06-30T15:00:00Z",
  },
  {
    id: "workspace-vendor",
    name: "Vendor workspace",
    purpose: "Vendor due diligence and document refresh run here.",
    environmentId: "prod-local",
    rehearsalEnvironmentId: "stub-local",
    systems: [
      grant("crm", "write", true),
      grant("document_store", "write"),
      grant("messaging", "write", true),
    ],
    clockMode: "wall",
    versions: [{ version: 1, note: "Created", createdAt: "2026-07-08T11:00:00Z" }],
    createdAt: "2026-07-08T11:00:00Z",
  },
];
