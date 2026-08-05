/**
 * The pure compatibility computation of the catalog install flow: a
 * routine's capability requirements crossed with a workspace's system
 * grants produce the CompatibilityReport the install screen renders
 * (DESIGN §6: green checks, mapping picker, connect prompts, and the
 * "portable except N vendor-specific tools" line).
 *
 * This mirrors the E3 computation the environments service will own;
 * until it does the website computes the report itself over the typed
 * workspace shape. TODO(environments-E0): replace this module's callers
 * with the service's report once the registry serves one.
 *
 * Matching model:
 * - Requirements name a capability family ("crm"), workspaces grant
 *   concrete systems ("crm" today, "crm:salesforce" once vendors split).
 *   A grant satisfies a requirement when its family (the id up to the
 *   first ":") matches and its scope covers the requirement (a write
 *   requirement needs a write grant; read is satisfied by either).
 * - Exactly one satisfying grant is a green check; several is a mapping
 *   choice ("needs a mapping" picker); none is a connect prompt, which
 *   also covers a connected system granted too narrowly.
 * - Vendor-specific tools are the routine's tools pinned to a concrete
 *   vendor system. Ones whose vendor the workspace connects are fine;
 *   the rest are counted for the portability line.
 */

import type { CompatibilityReport, Workspace } from "@/lib/api/environments";

export interface RequiredSystem {
  /** Capability family the routine needs, e.g. "crm". */
  systemId: string;
  scope: "read" | "write";
}

export interface VendorSpecificTool {
  /** Tool name, internal only; never rendered in lists. */
  tool: string;
  /** The concrete vendor system the tool is pinned to. */
  vendorSystemId: string;
}

export interface CapabilityRequirements {
  systems: RequiredSystem[];
  vendorSpecificTools: VendorSpecificTool[];
}

/** Capability family of a system id: "crm:salesforce" -> "crm". */
export function systemFamily(systemId: string): string {
  const separator = systemId.indexOf(":");
  return separator === -1 ? systemId : systemId.slice(0, separator);
}

/** Whether a granted scope covers a required one. */
function scopeCovers(granted: "read" | "write", required: "read" | "write"): boolean {
  return required === "read" || granted === "write";
}

/** Requirements x workspace grants -> the report the install flow renders. */
export function computeCompatibility(
  requirements: CapabilityRequirements,
  workspace: Pick<Workspace, "id" | "systems">,
): CompatibilityReport {
  const greenChecks: string[] = [];
  const mappingChoices: Array<{ systemId: string; options: string[] }> = [];
  const connectPrompts: string[] = [];

  for (const required of requirements.systems) {
    const family = systemFamily(required.systemId);
    const candidates = workspace.systems.filter(
      (grant) => systemFamily(grant.systemId) === family && scopeCovers(grant.scope, required.scope),
    );
    if (candidates.length === 0) {
      connectPrompts.push(required.systemId);
    } else if (candidates.length === 1) {
      greenChecks.push(required.systemId);
    } else {
      mappingChoices.push({
        systemId: required.systemId,
        options: candidates.map((grant) => grant.systemId),
      });
    }
  }

  const connected = new Set(workspace.systems.map((grant) => grant.systemId));
  const vendorSpecificToolCount = requirements.vendorSpecificTools.filter(
    (tool) => !connected.has(tool.vendorSystemId),
  ).length;

  return {
    workspaceId: workspace.id,
    greenChecks,
    mappingChoices,
    connectPrompts,
    vendorSpecificToolCount,
  };
}

/** One word for where a report stands; drives the install confirm state. */
export function reportState(report: CompatibilityReport): "green" | "needs_mapping" | "needs_connect" {
  if (report.connectPrompts.length > 0) return "needs_connect";
  if (report.mappingChoices.length > 0) return "needs_mapping";
  return "green";
}

/**
 * Parse a stored routine_version_ref ("routine-x@v3") into its parts.
 * The catalog pins installs to these snapshot versions; "update available"
 * is computed by comparing them.
 */
export function parseVersionRef(ref: string): { routineId: string; version: number } {
  const at = ref.lastIndexOf("@v");
  const routineId = at === -1 ? ref : ref.slice(0, at);
  const version = at === -1 ? 0 : Number(ref.slice(at + 2));
  return { routineId, version: Number.isFinite(version) ? version : 0 };
}

/** Build the stored routine_version_ref for a snapshot publish. */
export function versionRef(routineId: string, version: number): string {
  return `${routineId}@v${version}`;
}
