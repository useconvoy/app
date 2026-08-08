# environments

The bridge between agent runtimes and the real world. Aneesh's runtime (devbox on E2B) reaches outside through exactly two doors, both owned here:

1. **The governed tool gateway** — terminates MCP. Trusted runtime activities (run_turn / promoted-tool activities — never sandboxed agent code) speak JSON-RPC MCP to the binding's per-connection doors with a per-run JWT; the gateway resolves the run's environment@version, enforces per-tool allowlists and the manifest's execution/side-effecting flags, injects credentials at the edge (sandboxes hold no credentials, the runtime never reads tokens), and records the audit envelope into the shared Postgres event log with idempotency-key dedupe that makes runtime activity retries double-fire-safe.
2. **The browser egress proxy** — the sandbox browser's only network path, enforcing the environment's domain allowlist. Login credentials are filled by a TRUSTED fill service driving the sandbox browser's CDP from outside, via gateway-issued leases; values never enter the sandbox filesystem or model context.

Spec: `convoy-environments-spec.md` (Desktop, v0.1 + settled decisions). Contracts: the event taxonomy in `core/convoy_core` — co-signed, changes need both founders.

## Concepts

> **Vocabulary.** The deployed website's words win on user-facing surfaces: the tenant is an **organization** (our db `workspaces` table, wire `tenant_id`) and a bundle of system grants with production + rehearsal bindings is a **workspace** (our db `environments` table, wire `environment_id`). The console API speaks that language (`/organizations/{org}/workspaces/...`); the machine seam (`EnvironmentBinding`, `GET /environments/{id}`, `/internal/*`, `/data-plane/*`, `/mcp/*`) is frozen and keeps its names. The concepts below use the code's names.

- **Connection** (admin-owned, workspace-level): an authenticated link to one external system — Slack/Notion/GitHub token, a remote MCP server, a browser login. Carries a tool **manifest** with per-tool `execution` (`inline` | `promoted`) and `sideEffecting` flags, hashed as `manifest_hash`.
- **Workspace (console term; wire: environment)** (builder-owned, versioned-immutable): a policy bundle subsetting connections — explicit tool allowlists, promote escalations, browser domain allowlist, sandbox template, data namespace. Users see "workspace"; this service and the frozen runtime contract keep calling it an environment (`environment_id`, `EnvironmentBinding`). `policy_hash` feeds the certified tuple; edits create a new version, so certification is voided explicitly, never silently. Runs pin `(environment_id, version)` via the binding.
- **Tool flags** (runtime DESIGN §5 vocabulary; the trust obligation is ours): `execution="inline"` + `sideEffecting=False` → one collapsed `tool_call` event, safe to re-run inside `run_turn`; `execution="promoted"` / side-effecting → `tool_intent → tool_executed → tool_result` deduped on the runtime's idempotency key `hash(run_id, step_id, turn, call_index)` (errored results never dedupe). `promoteOverrides` escalate per environment; nothing ever downgrades. Budgets and human gates are runtime-owned (workflow `BudgetState` + plan-step `HumanGate`) — this layer meters nothing and parks nothing.

Naming across the console, this service, and the runtime wire is mapped in [docs/LEXICON.md](../docs/LEXICON.md) — the normative table for naming disputes.

## Layout

| module | what |
|---|---|
| `schema/` | control-plane Pydantic models, canonical `manifest_hash` / `policy_hash` |
| `db/` | SQLAlchemy tables (spec §3) + the shared `events` table (the DDL proposal) + `SqlEventLog` honoring the agent-evals EventLog contract |
| `secrets/` | write-only secrets service; builtin envelope-encryption backend (1Password/KMS slots later) |
| `connectors/` | slack, notion, github (token-based), google (service-account JWT grant: Drive list, Sheets read/append) + `mcp_custom` (BYO remote MCP; unannotated tools default to promoted + side-effecting) |
| `gateway/` | per-run JWTs, policy engine (fail-closed on manifest drift), GatewayService, MCP termination, binding registry, credential leases |
| `console_api/` | the website-vocabulary surface: organizations (tenants), memberships, connections, versioned workspaces (= the runtime's environments; `workspaceId == environmentId`), grants (viewer/operator/env_admin) |
| `devbox/` | `convoy-egress-proxy` (in-sandbox, secret-free) + `convoy-fill-sidecar` (trusted stack service) |

## Quickstart

```bash
python3 -m venv .venv && .venv/bin/pip install -e ../core -e ".[dev]"
.venv/bin/python -m pytest tests            # everything runs on SQLite, no services needed

docker compose up -d                        # local Postgres
export CONVOY_DATABASE_URL=postgresql+psycopg2://convoy:convoy@localhost:5432/convoy
export CONVOY_MASTER_KEY=$(.venv/bin/convoy-environments generate-master-key)
export CONVOY_GATEWAY_SECRET=$(openssl rand -hex 32)
export CONVOY_INTERNAL_TOKEN=$(openssl rand -hex 32)
.venv/bin/convoy-environments init-db       # or: alembic upgrade head
.venv/bin/convoy-environments serve         # /gateway/* and /console/* on :8780
```

## Runtime interface (for agent-runtime)

- `GET /gateway/internal/environments/{id}/binding[?version=N&kind=production|sandbox]` (header `X-Convoy-Internal`) → the frozen `EnvironmentBinding` snapshot (`convoy_core.binding`, DESIGN §5 verbatim incl. `ClockConfig`). One environment definition compiles into two bindings: `production` (real clock) and `sandbox` (virtual-capable clock, fails closed with 409 while side-effecting connector tools lack mocks). When the environment sets a `sandbox_template`, both kinds also grant `sandbox_exec` (promoted, side-effecting, scope `sandbox:<template>`) — the runtime routes it by its `sandbox_` prefix to its own SandboxProvider, so it has no `connector_endpoints` door and is exempt from the sandbox mock requirement (sandbox jobs run inside the sandbox with effects journaled in the workspace, never touching a production connector). Unpinned resolves latest; pin the returned `id` (`env_x@3/production`) as `RunState.binding_ref`. Every URL in `connector_endpoints` is a gateway door (`/mcp/{connection_id}`), per the fulfillment clause — never a direct connector server.
- `POST /gateway/internal/run-tokens` (header `X-Convoy-Internal`) → per-run JWT scoped to `(run, mission, workspace, environment@version)`. It stays with trusted runtime workers — sandboxes never receive tokens or credentials.
- Runtime activities speak MCP to the per-connection doors from the binding (`POST /gateway/mcp/{connection_id}`) or the aggregate `POST /gateway/mcp` (`Authorization: Bearer <run-jwt>`). Scoped doors filter both discovery and dispatch. Promoted calls carry the runtime's idempotency key in `_meta.idempotencyKey`; a retried activity gets the recorded result, never a second side effect.
- Bake `convoy-egress-proxy` into the sandbox image (holds no secrets; Chromium launches with `--proxy-server=http://127.0.0.1:3128`); run `convoy-fill-sidecar` in the trusted stack pointed at the sandbox's CDP endpoint (env-var config: `CONVOY_GATEWAY_URL`, `CONVOY_RUN_TOKEN`, `CONVOY_CDP_URL`).

## Accounts and sign-in, in plain English

**How someone logs in.** The website signs people in with WorkOS (company SSO / email login). On every console request, the website forwards that login token; the console checks the token is genuinely from WorkOS (cryptographic signature, not trust) and then looks the person up in its own user list. The first time someone signs in with an email an admin has invited, their WorkOS identity gets connected to that user automatically — after that they're recognized by identity alone. If nobody invited them, they're turned away with a note to ask an admin: **logging in never creates an account by itself.** For local development with no WorkOS configured, the old `X-Convoy-User` header keeps working; the moment WorkOS is configured, headers stop being accepted.

**How the website's server calls us.** Besides forwarding the person's own WorkOS token, the website's backend can act on a signed-in user's behalf service-to-service: `X-Convoy-Internal` (the same internal/provisioning shared secret the gateway uses) plus `X-Convoy-Acts-For: <email>`. The email must belong to an already-invited user — a bad token is a 401, an unknown email a 403, and this path never creates accounts.

**How teammates get added.** An organization admin invites people by email with a role attached (`admin` — manages connections, secrets, and people; `builder` — creates workspaces; `member` — baseline). The invite is just a row in our database — no email is sent from this layer (the website owns notifications) — and it takes effect the moment the person first signs in with that email. Re-inviting the same email changes their role rather than creating a duplicate. Everyone can see the member list; only admins can change it.

**How an organization gets created.** Not self-serve, on purpose. An organization is a customer: creating one goes hand in hand with provisioning that customer's infrastructure stack, so `POST /organizations` requires a provisioning secret only the founders/deploy tooling hold. If the secret isn't configured at all, organization creation is simply off.

## Deliberately not here (runtime-owned per DESIGN v1)

Budgets (workflow `BudgetState` + LiteLLM caps) · human gates and approvals (plan-step `HumanGate`, `human_response` signals; gate UX is website/) · runs/missions state · sandbox lifecycle (`SandboxProvider` impls) · idempotency-key minting (we only honor them).

## Deliberately not here (deferred)

OAuth connector wizard (white-glove CLI/API for the first partners) · Google OAuth connectors · 1Password/KMS backends (interface exists) · mock registry for sandbox bindings (sandbox compilation fails closed until it lands) · Okta/SCIM agent identities · screenshot masking during fill.
