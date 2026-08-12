# Convoy Lexicon — the rosetta stone

The product and the frozen runtime wire use different names, and the
translation must stay explicit. Vinayaka's vocabulary ruling: **the deployed website's
vocabulary is canonical for user-facing surfaces**, and **the machine/wire
layer does not rename** (the runtime contract is frozen). This table is the
translation between them; when a naming dispute comes up, this table is
normative.

The website already enforces its column mechanically: `website/src/lexicon.ts`
is "the only door between internal vocabulary and rendered copy" (its `terms`
map is the source for the first column here, and `npm run check:lexicon` fails
the build when an internal noun reaches rendered output). This document
extends that mapping across the backend so docs and services can translate in
both directions.

| Console / website term | environments service | runtime wire (frozen) |
|---|---|---|
| **organization** — the tenant/customer | db table `organizations` + `org_id` in the console API (`/organizations/{org_id}/…`) | `tenant_id` |
| **workspace** — a shared named scope for system connections and grants; many Agents may use it | a base row in db table `environments` (`parent_environment_id IS NULL`) + `workspaceId` in the console API | connector grants compiled into an `EnvironmentBinding` |
| **agent** — the goal, instructions, schedule, and runtime setup for one worker; it references one shared workspace | a child row in db table `environments` (`parent_environment_id = workspaceId`) reached through the internal nested `/workspaces/{workspaceId}/environments` API, enriched by the website `agents` row | `AgentSpec` + `environment_id` + immutable `EnvironmentBinding` (DESIGN §5) |
| **rehearsal copy** | `?kind=sandbox` binding compiled for an agent's runtime setup | `kind="sandbox"` + virtual `ClockConfig` |
| **system** | connector + connection (an authenticated link to one external system) | `connector_endpoints` entry (a gateway MCP door) |
| **system grant** | connection + `tool_allowlist` | `ToolGrant` |
| **stand-in** | deterministic connector fixture selected by a sandbox binding | `simulated_effect` audit event + idempotent simulated data plane |
| **run** | — | `run_id` + `RunState` |
| **checkpoint** | — (gates are runtime-owned) | gate (`HumanGate`, `gate_opened`/`gate_answered` events) |

## Rules

1. **User-facing surfaces use console vocabulary exclusively.** The website,
   marketing pages, notifications, and anything else a customer reads say
   organization, workspace, agent, rehearsal copy, system, stand-in,
   checkpoint — never tenant, sandbox, connector, mock, environment,
   or gate.
   On the website this is build-enforced through
   `website/src/lexicon.ts`.
2. **Wire names are frozen.** `tenant_id`, `environment_id`,
   `EnvironmentBinding`, the runtime binding-resolution surface
   (`GET …/environments/{id}/binding`), `RunState.binding_ref`, and the rest
   of the runtime contract keep their names. Only Aneesh's decision log
   (per SERVICE-CONTRACTS §0's amendment rule) can change them.
3. **When writing docs, qualify ambiguous uses.** The same word can point at
   different objects across layers, so disambiguate inline: "workspace
   (console)" vs the environments service's `organizations` table;
   "agent runtime setup (console)" vs `EnvironmentBinding` (the frozen wire
   snapshot it compiles into).

## Compatibility transition

Migration `0011_agents_runtime.sql` renames existing website environment rows
to agents without changing their ids or immutable registry bindings. Migration
`0012_agent_automation.sql` folds each former job definition into an Agent and
removes the `routines` table. A run never presents a separate environment
picker: it freezes the chosen Agent's rehearsal or production binding plus the
grants from the shared Workspace that Agent references.
