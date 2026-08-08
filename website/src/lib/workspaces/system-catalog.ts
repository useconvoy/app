/**
 * The system catalog: which systems a workspace can connect, whether a
 * write through each one touches the world outside the organization, and
 * the stand-in note that rides every side-effecting write grant. This is
 * product configuration, not per-org data; the create-workspace picker and
 * the catalog install screens read it, and grantsFromChoices turns a
 * picker submission into stored grants while holding the stand-in line.
 *
 * TODO(environments-E0): the environments directory becomes the source of
 * truth for connectable systems; this module then reads from it.
 */

import type { SystemGrant } from "@/lib/api/environments";

export interface SystemDefinition {
  id: string;
  displayName: string;
  /** True when granting write would touch the world outside the org. */
  sideEffecting: boolean;
  /** Plain-language stand-in note for side-effecting systems. */
  standInNote: string | null;
}

export const systemCatalog: SystemDefinition[] = [
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

export function catalogSystem(systemId: string): SystemDefinition | undefined {
  return systemCatalog.find((system) => system.id === systemId);
}

export interface GrantChoice {
  systemId: string;
  scope: "read" | "write";
  useStandIn: boolean;
}

/**
 * Picker choices -> stored grants. The create modal blocks a live
 * side-effecting write client-side; this holds the same line so no caller
 * can slip one past the form.
 */
export function grantsFromChoices(choices: GrantChoice[]): SystemGrant[] {
  return choices.map((choice) => {
    const system = catalogSystem(choice.systemId);
    if (!system) throw new Error(`unknown system: ${choice.systemId}`);
    const needsStandIn = system.sideEffecting && choice.scope === "write";
    if (needsStandIn && !choice.useStandIn) {
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
}
