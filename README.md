# Convoy

**Employees spawn personal agents; companies need company agents.** Convoy is where a company creates, permissions, tests, and runs its fleet of routine-work agents — LLMs that reason and make decisions, execute only the actions their environment allows, and flag consequential decisions for human approval.

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

## The 6-minute demo path

1. **Cold open** — `/` fleet dashboard: 47 leads researched, 6 deals papered, 12 doc drafts, 31 records cleaned overnight.
2. **The frame** — `/environments`: per-environment connectors, credentials, and the readable policy rule list.
3. **Hire an agent** — `/agents` → start from the Closed-Won Paperwork template → save v1 → bound to Sandbox (binding validates every tool grant).
4. **Test in Sandbox** — `/systems` → mark the *Latch Robotics* sandbox deal Closed-Won → watch the live trace stream → then run the **8-scenario suite** from the agent page; every assertion checks actual sandbox system state.
5. **Promote** — the pre-flight diff modal: credential swap, policy delta (`email.send → requires approval`), test status on this exact version. Promotion is blocked unless the suite is green.
6. **Production pauses** — `/systems` (Production) → close the *Northwind Systems* deal (22% discount, external contact). The run pauses twice: once **agent-flagged** (discount over threshold), once **policy-gated** (external email). Approve from `/approvals`; reject to see the clean termination path.
7. **Audit & kill switch** — `/audit`: any action to its full causal chain in two clicks; fleet-wide pause per agent from the dashboard.

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
