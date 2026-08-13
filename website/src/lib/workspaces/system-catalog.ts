/**
 * The system catalog: which systems a workspace can connect, whether a
 * write through each one touches the world outside the organization, and
 * the stand-in note that rides every side-effecting write grant. This is
 * product configuration, not per-org data; the create-workspace picker and
 * the catalog install screens read it, and grantsFromChoices turns a
 * picker submission into stored grants while holding the stand-in line.
 *
 * Since environments-E0 each system also carries its registry connection
 * mapping (provider plus declared tool manifest) when the environments
 * service has a provider for it; the adapter in lib/api/environments uses
 * that to ensure connections and translate grant scopes to allowlists.
 * The registry eventually becomes the source of truth for this whole
 * catalog; until then this module is where it lives.
 */

import type { SystemGrant } from "@/lib/api/environments";

/**
 * One tool a system's registry connection declares. Mirrors the registry's
 * per-tool vocabulary: "inline" tools are read-only and run inside a turn;
 * "promoted" tools mutate and run as their own retry-safe activity. A
 * grant scoped "read" allowlists only the inline read tools; "write"
 * allowlists everything.
 */
export interface SystemToolDeclaration {
  name: string;
  execution: "inline" | "promoted";
  sideEffecting: boolean;
}

/**
 * How a system materializes in the environments registry: the provider a
 * managed connection is created under, and the declared tool manifest for
 * that connection. Only systems whose provider the registry actually
 * serves carry one; the tool names here mirror that provider's real
 * manifest exactly so a later credentialed refresh agrees with them.
 */
export interface SystemConnectionSpec {
  provider: string;
  tools: SystemToolDeclaration[];
}

export interface SystemDefinition {
  id: string;
  displayName: string;
  /** True when granting write would touch the world outside the org. */
  sideEffecting: boolean;
  /** Plain-language stand-in note for side-effecting systems. */
  standInNote: string | null;
  /** Registry connection mapping; null while no provider serves this system. */
  connection: SystemConnectionSpec | null;
}

export const systemCatalog: SystemDefinition[] = [
  {
    id: "identity_provider",
    displayName: "Identity provider",
    sideEffecting: false,
    standInNote: null,
    connection: null,
  },
  {
    id: "hris",
    displayName: "HR system",
    sideEffecting: false,
    standInNote: null,
    connection: null,
  },
  {
    id: "document_store",
    displayName: "Google Drive",
    sideEffecting: false,
    standInNote: null,
    connection: {
      provider: "google",
      tools: [
        { name: "google.drive_list_files", execution: "inline", sideEffecting: false },
        { name: "google.sheets_read_range", execution: "inline", sideEffecting: false },
        { name: "google.docs_read", execution: "inline", sideEffecting: false },
        // Mutating, so promoted and flagged in the registry's conservative
        // vocabulary even though a write here stays inside the org.
        { name: "google.sheets_append_row", execution: "promoted", sideEffecting: true },
        { name: "google.docs_update", execution: "promoted", sideEffecting: true },
      ],
    },
  },
  {
    id: "messaging",
    displayName: "Slack",
    sideEffecting: true,
    standInNote: "Stand-in for Slack: messages are held in the outbox instead of being sent.",
    connection: {
      provider: "slack",
      tools: [
        { name: "slack.list_channels", execution: "inline", sideEffecting: false },
        { name: "slack.read_messages", execution: "inline", sideEffecting: false },
        { name: "slack.post_message", execution: "promoted", sideEffecting: true },
      ],
    },
  },
  {
    id: "crm",
    displayName: "CRM",
    sideEffecting: true,
    standInNote: "Stand-in for CRM: record changes are noted for review instead of being applied.",
    connection: null,
  },
  {
    id: "code_host",
    displayName: "GitHub",
    sideEffecting: true,
    standInNote: "Stand-in for GitHub: issues are recorded for review instead of being opened.",
    connection: {
      provider: "github",
      tools: [
        { name: "github.list_issues", execution: "inline", sideEffecting: false },
        { name: "github.get_file", execution: "inline", sideEffecting: false },
        { name: "github.create_issue", execution: "promoted", sideEffecting: true },
      ],
    },
  },
  {
    id: "knowledge_base",
    displayName: "Notion",
    sideEffecting: true,
    standInNote: "Stand-in for Notion: pages are drafted for review instead of being created.",
    connection: {
      provider: "notion",
      tools: [
        { name: "notion.search", execution: "inline", sideEffecting: false },
        { name: "notion.get_page", execution: "inline", sideEffecting: false },
        { name: "notion.create_page", execution: "promoted", sideEffecting: true },
      ],
    },
  },
];

export function catalogSystem(systemId: string): SystemDefinition | undefined {
  return systemCatalog.find((system) => system.id === systemId);
}

export interface GrantChoice {
  systemId: string;
  scope: "read" | "write";
  useStandIn: boolean;
  /**
   * Explicitly enabled actions (tool names) for this connector in this
   * workspace. Absent keeps the scope-derived surface: every declared
   * tool rides the grant and the scope decides the allowlist.
   */
  tools?: string[];
}

/**
 * Picker choices -> stored grants. Custom systems (an org's own tool
 * servers, ids "custom:<connection>") resolve through definitions supplied
 * by the caller. Write-capable grants carry explanatory rehearsal metadata,
 * but the production binding still targets the real provider connector.
 */
export interface CustomSystemDefinition {
  id: string;
  displayName: string;
  sideEffecting: boolean;
  standInNote: string | null;
  tools?: Array<{ name: string; sideEffecting: boolean }>;
}

export function grantsFromChoices(
  choices: GrantChoice[],
  customSystems: CustomSystemDefinition[] = [],
): SystemGrant[] {
  const customById = new Map(customSystems.map((system) => [system.id, system]));
  return choices.map((choice) => {
    const system = catalogSystem(choice.systemId) ?? customById.get(choice.systemId);
    if (!system) throw new Error(`unknown system: ${choice.systemId}`);
    const needsStandIn = system.sideEffecting && choice.scope === "write";
    const grant: SystemGrant = {
      systemId: system.id,
      displayName: system.displayName,
      scope: choice.scope,
      sideEffecting: system.sideEffecting,
    };
    const declaredTools =
      "connection" in system ? system.connection?.tools : system.tools;
    if (declaredTools && declaredTools.length > 0) {
      // An explicit action selection narrows the grant to those tools;
      // names the connector never declared are simply not grantable.
      const selected = choice.tools ? new Set(choice.tools) : null;
      const granted = selected
        ? declaredTools.filter((tool) => selected.has(tool.name))
        : declaredTools;
      grant.tools = granted.map((tool) => ({
        name: tool.name,
        sideEffecting: tool.sideEffecting,
      }));
    }
    if (needsStandIn && system.standInNote) {
      grant.standIn = { note: system.standInNote };
    }
    return grant;
  });
}

/**
 * Resolve the concrete connector tools a routine may request from its
 * workspace. The workspace grant is authoritative for scope: read grants
 * receive only inline/read tools, while write grants receive the full pinned
 * provider surface. Systems without a registry provider intentionally add no
 * tools instead of inventing a capability the environment binding cannot
 * honor.
 */
export function toolIdsForRoutine(
  requiredSystemIds: readonly string[],
  workspaceGrants: readonly SystemGrant[],
): string[] {
  const grants = new Map(workspaceGrants.map((grant) => [grant.systemId, grant]));
  const tools = new Set<string>();
  for (const systemId of requiredSystemIds) {
    const grant = grants.get(systemId);
    if (!grant) continue;
    const declaredTools = grant.tools ?? catalogSystem(systemId)?.connection?.tools ?? [];
    for (const tool of declaredTools) {
      if (grant.scope === "write" || !tool.sideEffecting) tools.add(tool.name);
    }
  }
  return [...tools].sort();
}
