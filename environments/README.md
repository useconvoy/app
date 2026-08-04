# environments

The bridge between agent runtimes and the real world. Aneesh's runtime (devbox on E2B) reaches outside through exactly two doors, both owned here:

1. **The governed tool gateway** — terminates MCP. The agent speaks JSON-RPC MCP to `/mcp` with a per-run JWT; the gateway resolves the run's environment@version, enforces per-tool allowlists and effect classes, injects credentials at the edge (agents never hold secrets), records the two-phase event envelope into the shared Postgres event log, and parks gated calls until console approval.
2. **The browser egress proxy** — the devbox browser's only network path, enforcing the environment's domain allowlist. Login credentials are filled by a sidecar over CDP via gateway-issued leases; values never enter model context.

Spec: `convoy-environments-spec.md` (Desktop, v0.1 + settled decisions). Contracts: the event taxonomy in `core/convoy_core` — co-signed, changes need both founders.

## Concepts

- **Connection** (admin-owned, workspace-level): an authenticated link to one external system — Slack/Notion/GitHub token, a remote MCP server, a browser login. Carries a tool **manifest** with per-tool `effectClass` (`read` | `effectful` | `gated`), hashed as `manifest_hash`.
- **Environment** (builder-owned, versioned-immutable): a policy bundle subsetting connections — explicit tool allowlists, gate escalations, browser domain allowlist, budget defaults. `policy_hash` feeds the certified tuple; edits create a new version, so certification is voided explicitly, never silently. Missions pin `(environment_id, version)`.
- **Effect classes**: `read` → one collapsed `tool_call` event; `effectful` → `tool_intent → tool_executed → tool_result` with idempotency-key dedupe (errored results never dedupe); `gated` → intent + `gate_raised`, parked; approval on the console leads to `tool_approved → tool_executed → tool_result` on retry with the same key.

## Layout

| module | what |
|---|---|
| `schema/` | control-plane Pydantic models, canonical `manifest_hash` / `policy_hash` |
| `db/` | SQLAlchemy tables (spec §3) + the shared `events` table (the DDL proposal) + `SqlEventLog` honoring the agent-evals EventLog contract |
| `secrets/` | write-only secrets service; builtin envelope-encryption backend (1Password/KMS slots later) |
| `connectors/` | slack, notion, github (token-based) + `mcp_custom` (BYO remote MCP; unannotated tools default to `effectful`) |
| `gateway/` | per-run JWTs, policy engine (fail-closed on manifest drift), GatewayService, MCP termination, credential leases |
| `console_api/` | workspaces, connections, versioned environments, grants (viewer/operator/env_admin), console-first gates |
| `devbox/` | shipped components for the E2B image: `convoy-egress-proxy`, `convoy-fill-sidecar` |

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

- `GET /gateway/internal/environments/{id}/binding[?version=N]` (header `X-Convoy-Internal`) → the frozen `EnvironmentBinding` snapshot (`convoy_core.binding`). Unpinned resolves latest; pin the returned `id` (`env_x@3`) as `RunState.binding_ref`. Every URL in `connector_endpoints` is a gateway door (`/mcp/{connection_id}`), per the fulfillment clause — never a direct connector server.
- `POST /gateway/internal/run-tokens` (header `X-Convoy-Internal`) → per-run JWT scoped to `(run, mission, workspace, environment@version)`; hand it to the devbox.
- The devbox agent speaks MCP to the per-connection doors from the binding (`POST /gateway/mcp/{connection_id}`) or the aggregate `POST /gateway/mcp` (`Authorization: Bearer <run-jwt>`). Scoped doors filter both discovery and dispatch. A parked gate comes back as a successful tool result with `structuredContent: {status: "parked", gateId, idempotencyKey}` — land state and die; retry with the same `idempotencyKey` in `_meta` after resolution.
- Gate resolutions appear in the event log (`gate_resolved`); the runtime's scheduler watches for them to resume missions.
- Bake `convoy-egress-proxy` + `convoy-fill-sidecar` into the devbox image (env-var config: `CONVOY_GATEWAY_URL`, `CONVOY_RUN_TOKEN`, `CONVOY_ALLOWED_DOMAINS`, `CONVOY_CDP_URL`); launch Chromium with `--proxy-server=http://127.0.0.1:3128`.

## Deliberately not here (v1)

Runs/missions tables (runtime-owned) · OAuth connector wizard (white-glove CLI/API for the first partners) · Google OAuth connectors · 1Password/KMS backends (interface exists) · hermetic browser · Okta/SCIM agent identities · screenshot masking during fill (fill-then-agent-submits keeps values off-screen in the common path).
