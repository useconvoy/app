/**
 * Fixture data for the environments seam, used by tests only. Production
 * reads workspaces from the org-scoped workspaces table; these two demo
 * workspaces exist so component and unit suites can render and reason
 * about fully connected workspaces without a database. The runnable
 * environment ids ("prod-local", "stub-local", "stub-local-virtual") are
 * the real local runtime's stub registry, matching what production
 * bindings use.
 */

import type { SystemGrant, Workspace } from "@/lib/api/environments";
import { catalogSystem, type SystemDefinition } from "@/lib/workspaces/system-catalog";

/** The shared system catalog, re-exported for suites that assert over it. */
export { systemCatalog, catalogSystem } from "@/lib/workspaces/system-catalog";
export type FixtureSystem = SystemDefinition;

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
