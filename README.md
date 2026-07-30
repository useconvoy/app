# Convoy Labs

**Employees spawn personal agents; companies need company agents.** Convoy Labs is where a company creates, permissions, tests, and runs its fleet of routine-work agents — LLMs that reason and make decisions, execute only the actions their environment allows, and flag consequential decisions for human approval.

This repository is the working POC behind the investor demo: the full product spec and demo plan live in [`docs/product-spec.md`](docs/product-spec.md).

## Quick start

Requires Node.js 20.9+ (Next.js 16).

```bash
npm install
npm run dev
```

Open http://localhost:3000. The workspace self-seeds on first launch with **Meridian Labs**' convoy: two environments (Sandbox + Production), four connectors each, permission policies, four deployed agents, and the overnight fleet history from the demo's cold open.

No credentials, no database, no API keys required — the demo runs fully self-contained.

```bash
npm run reset   # one-command reseed (demo-day risk mitigation, spec §6.8)
```

### Account and mission control

The application also supports real accounts, workspaces, and AWS-backed
missions. Locally, control-plane records use `.data/control-plane.json`. When
`DATABASE_URL` is present they use PostgreSQL instead.

```bash
CONVOY_ACCOUNTS_ENABLED=1 \
CONVOY_SESSION_SECRET=replace-with-a-long-random-value \
npm run dev
```

Set `CONVOY_CLOUD_RUNTIME=1` plus the ECS and DynamoDB variables documented in
[`docs/control-plane-deployment.md`](docs/control-plane-deployment.md) to have
new missions launch the AWS/Temporal runtime.

## Deploy to a cloud server

```bash
export CONVOY_BASIC_AUTH="user:strong-password"   # required when internet-facing
docker compose up -d --build
```

One container, one volume, TLS in front. Set `ANTHROPIC_API_KEY` and
`CONVOY_LIVE_MODEL=1` to drive agents with the live model. Full guidance —
including why serverless platforms won't work and the M1 reference topology
for running agents with real permissions — is in
[`docs/deployment.md`](docs/deployment.md). The next-phase design lives in
[`docs/architecture-v2.md`](docs/architecture-v2.md).

## The guided demo path — 7 steps, about 8 minutes

The public landing page at `/` defines company agents and evaluates one seeded tool call under
Sandbox and Production policy side by side, without touching the demo store. `/start` then walks
the workspace in seven steps — the canonical list lives in `src/components/StartGuide.tsx`, and
these are the same seven:

1. **Read the fleet** — `/dashboard`: four company agents and the seeded overnight history.
2. **Compare the rules of the road** — `/environments`: per-environment connectors, credential
   handles, and the readable policy rule list. External email is refused in Sandbox and held for
   an approver in Production.
3. **Inspect an agent** — `/agents/agent_closed_won_paperwork`: trigger, tool grants, parameters,
   deployments, and the eight-scenario suite.
4. **Run it in Sandbox** — `/systems?env=env_sandbox&tab=crm` → mark the *Latch Robotics* deal
   Closed-Won → watch the live trace stream. *Changes sample state.*
5. **Earn the promotion** — run the suite, then open Promotion: the pre-flight diff shows the
   credential swap, the policy delta (`email.send → requires approval`), and test status for
   this exact version. Blocked unless green. *Changes sample state.*
6. **See two independent stops** — `/systems?env=env_production&tab=crm` → mark the *Northwind
   Systems* deal Closed-Won. The run pauses once agent-flagged (22% discount against a 15%
   standard) and once policy-gated (external invoice email). *Changes sample state.*
7. **Close the loop** — `/audit`: follow any action through its causal chain, then inspect the
   fleet-wide kill switch on `/dashboard`.

Steps 4–6 write to the shared sample workspace. If a deal is already Closed-Won, `/start` links
a completed run instead; `npm run reset` reseeds everything.

## Architecture

Per spec §6: control plane + agent runtime + **policy gateway** (the choke point). The runtime never touches credentials or external systems — it emits abstract tool calls; the gateway resolves run → deployment → environment, evaluates policy, injects environment-scoped credentials, executes, and writes an immutable trace row on every branch.

```
src/server/
  gateway.ts        policy gateway: evaluate → allow/deny/require_approval → trace
  connectors.ts     connector definitions + tool manifests (MCP-shaped seam)
  runtime/
    loop.ts         run state machine: queued → running → paused_pending_approval → …
    planner.ts      demo-mode engine: deterministic reason/act/flag per agent
    claude.ts       live-model engine (Claude API tool loop, persisted history)
  testing/harness.ts  scenario suite: fixture seed → trigger → assertions vs system state
  promotion.ts      pre-flight diff + green-suite promotion gate
  seed.ts           Meridian Labs workspace seed
  db.ts             JSON-persisted store (.data/db.json) — Postgres is the production seam
src/app/            Next.js control plane UI + API routes (SSE live traces)
```

### Demo mode vs. live model

By default the agent "model" is a deterministic planner (`planner.ts`) — the spec's demo-mode flag taken to its conclusion: zero external dependencies, zero flakes on stage, and the governance layer (gateway, policies, approvals, tests, audit) is fully real either way.

To drive agents with the real Claude API instead:

```bash
ANTHROPIC_API_KEY=sk-ant-... CONVOY_LIVE_MODEL=1 npm run dev
```

The live engine (`claude.ts`) runs a manual tool-use loop against `claude-opus-5`, exposes only gateway-governed tools plus the built-in `flag_for_review`, and persists message history per run so a paused run survives a restart (spec §6.4).

### Deliberate POC simplifications

- **Simulated external systems.** HubSpot/Gmail/Docs/Slack are DB-backed simulations with real tool manifests, viewable at `/systems`. The gateway is protocol-level, so swapping in real MCP connectors changes `gateway.ts`'s executor — nothing above it.
- **JSON store instead of Postgres.** Table shapes match spec §6.3 exactly; `db.ts` is the swap point.
- **Single workspace, two personas** — per the spec's non-goals.
