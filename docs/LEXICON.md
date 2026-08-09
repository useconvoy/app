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
| **organization** — the tenant/customer | db table `workspaces` (legacy name for organizations) + `org_id` in the console API (`/organizations/{org_id}/…`, branch org-vocabulary) | `tenant_id` |
| **workspace** — a named organizational scope for routines and system grants | a base row in db table `environments` (`parent_environment_id IS NULL`) + `workspaceId` in the console API | compatibility `environment_id` binding while no named environment exists |
| **environment** — the runtime configuration beneath a workspace: compute template, browser policy, and durable state namespace | a child row in db table `environments` (`parent_environment_id = workspaceId`) + nested `/workspaces/{workspaceId}/environments` API | `environment_id` + `EnvironmentBinding` (DESIGN §5) |
| **rehearsal copy** | `?kind=sandbox` binding compiled for an environment | `kind="sandbox"` + virtual `ClockConfig` |
| **system** | connector + connection (an authenticated link to one external system) | `connector_endpoints` entry (a gateway MCP door) |
| **system grant** | connection + `tool_allowlist` | `ToolGrant` |
| **stand-in** | mock registry (not yet built; sandbox compilation fails closed until it lands) | `simulated_effects/` outbox |
| **run** | — | `run_id` + `RunState` |
| **routine** | — | agent (`AgentSpec`) |
| **checkpoint** | — (gates are runtime-owned) | gate (`HumanGate`, `gate_opened`/`gate_answered` events) |

## Rules

1. **User-facing surfaces use console vocabulary exclusively.** The website,
   marketing pages, notifications, and anything else a customer reads say
   organization, workspace, environment, rehearsal copy, system, stand-in,
   routine, checkpoint — never tenant, sandbox, connector, mock, agent, gate.
   On the website this is build-enforced through
   `website/src/lexicon.ts`.
2. **Wire names are frozen.** `tenant_id`, `environment_id`,
   `EnvironmentBinding`, the runtime binding-resolution surface
   (`GET …/environments/{id}/binding`), `RunState.binding_ref`, and the rest
   of the runtime contract keep their names. Only Aneesh's decision log
   (per SERVICE-CONTRACTS §0's amendment rule) can change them.
3. **When writing docs, qualify ambiguous uses.** The same word can point at
   different objects across layers, so disambiguate inline: "workspace
   (console)" vs "`workspaces` table (legacy name for organizations)";
   "environment (console runtime configuration)" vs `EnvironmentBinding`
   (the frozen wire snapshot it compiles into).

## Compatibility transition

Older routines point directly at the compatibility binding stored on their
workspace. That remains a valid fallback. As soon as a named environment is
created, new runs select the workspace's default environment (or an explicit
environment chosen on the start-run screen). This lets the product introduce
the organization → workspace → environment hierarchy without invalidating
existing routine and run records.
