# agent-runtime

Durable execution for long-running, specialized agent runs. A run takes a goal,
produces a versioned plan, executes it turn by turn across days, survives
crashes and deploys, stays inside a dollar budget, pauses for humans where
required, and lands with auditable outputs.

Built on Temporal for durable orchestration, with all intelligence in
activities: workflow code is deterministic orchestration only, so the
replay/versioning surface stays thin.

## Architecture

```
website / API clients
        │ REST + SSE
        ▼
FastAPI control plane ──── start_workflow / signals ───▶ Temporal
        │                                                   │
        │ reads                                   AgentRunWorkflow
        ▼                                         plan · budget · steer
Postgres projections ◀── RunEvent outbox ── activities ──▶ run_turn (LLM turn
(RLS on tenant_id)                              │           + inline tools)
                                                │         promoted tools /
                                                │         sandbox jobs
                                                ▼
                                     S3 artifacts (transcripts,
                                     workspaces, snapshots, reports)
```

- **Write path:** external actions are signals into the root workflow; all
  real-world work happens in activities; everything large is claim-checked to
  S3 — only `ArtifactRef` pointers cross Temporal boundaries, and every
  payload is client-side encrypted by the codec before leaving the worker.
- **Read path:** the API never queries Temporal. Activities emit `RunEvent`s
  through an idempotent outbox into Postgres projections; clients read
  projections and subscribe over SSE.
- **Turns:** one `run_turn` activity = one LLM turn plus its inline
  (read-only, idempotent) tool calls, executed through a `TurnExecutor` seam —
  a deterministic scripted executor for tests and a Pydantic AI executor for
  real models, routed through a LiteLLM proxy with per-run virtual keys.
  Long or side-effecting tool calls are *promoted*: the workflow runs them as
  their own activities with explicit timeouts and idempotency keys, then
  re-enters the turn with the result.
- **Humans:** pause/resume, steer (note or redirect), plan approval, and
  per-step human gates with durable timeouts are first-class signals, and
  every state change lands in the event log with its actor.
- **Fan-out:** plan steps can execute as subagent child workflows with scoped
  briefs, budget-slice reservations, and compacted results — a parent never
  ingests a child transcript.
- **Multi-day context:** a pinned header (goal, criteria, plan, budget,
  steers) plus structured per-step summaries plus the current step's working
  transcript, compacted at step ends and mid-step token thresholds. Raw
  transcripts are archived, never deleted. Runs hop via `continue_as_new` at
  a turn limit with lossless state carry.
- **Virtual time:** sandbox-kind environments may run on a virtual clock —
  advanced manually via the API, automatically when idle, or on a wall-clock
  ratio — so a five-day rehearsal compresses to minutes. Events carry both
  real and virtual timestamps.
- **Sandboxes:** promoted jobs run through a `SandboxProvider` seam — a local
  subprocess provider for development and CI, and an ECS Fargate provider for
  stacks where sandbox tasks are credential-free (data moves only over
  short-lived presigned URLs minted by trusted workers). Workspaces are
  cache; S3 snapshots are truth.

## Layout

```
src/convoy_runtime/
  workflows/       # AgentRunWorkflow, SubagentWorkflow, plan validation — deterministic only
  activities/      # plan, turns, compaction, promoted tools, subagents, land, outbox
  control_plane/   # FastAPI app: run lifecycle endpoints + SSE event stream
  providers/       # turn executors, model gateway, artifact store, sandbox providers
  projections/     # Postgres schema, RLS-scoped connections, event folds
  clock.py         # RunClock seam: passthrough, virtual, and ratio clocks
  codec.py         # payload encryption for everything that enters Temporal
  carry.py         # runtime state carried across continue_as_new hops
docker/            # compose images: mock model, stub environment, LiteLLM proxy
infra/             # Terraform module, deploy pipeline, operator runbook (see infra/README.md)
tests/             # unit, workflow, activity, integration, contracts, scenarios,
                   # histories (replay), chaos, stub_env
```

Shared datatypes (`RunState`, `Plan`, `ToolGrant`, `RunEvent`, …) live in the
monorepo's `core/` package (`convoy_core`) and are imported, never redefined.

## Development

Prerequisites: [uv](https://docs.astral.sh/uv/) and Docker with the compose
plugin. Python 3.12 is provisioned by uv.

```sh
uv sync                # install the workspace
make lint              # ruff format check + lint
make typecheck         # pyright strict
make test              # fast lane: unit + workflow + activity + contracts +
                       #   scenarios + replay histories (no containers)
make e2e               # brings up the compose stack and runs integration tests
make chaos             # crash/durability suite against the compose stack
make live-smoke        # one real-model run through the proxy; skips without a
                       #   provider key (ANTHROPIC_API_KEY or OPENAI_API_KEY)
```

The compose stack (`agent-runtime/compose.yaml`) runs Temporal dev server,
Postgres (with row-level security), MinIO, a scriptable OpenAI-compatible mock
model, a stub environment service (tool registry + side-effect journal), and a
LiteLLM proxy. Deterministic lanes never call a real model.

### Invariants worth knowing before changing code

- Workflow code does no I/O, reads no clock or env, and uses no randomness;
  all time goes through `RunClock` (an AST lint test enforces this).
- Signals validate and enqueue only; the loop drains mailboxes at boundaries.
  Pause never cancels an in-flight activity.
- Every state change emits a `RunEvent` through the outbox; reads come from
  projections, never Temporal.
- Side-effecting calls carry `hash(run_id, step_id, turn, call_index)` as an
  idempotency key; inline tools must be read-only idempotent.
- Checked-in replay histories under `tests/histories/` are merge-blocking:
  changing workflow code means re-recording (with rationale) or gating the
  change behind `workflow.patched`.
- Every row is tenant-scoped and RLS is active even in dedicated stacks; DB
  access goes through `tenant_connection`.

## Configuration

The runtime reads its environment at startup (`config.py`): Temporal target
(`TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`, optional mTLS PEMs, optional
`TEMPORAL_WORKER_BUILD_ID` for versioned worker deploys), `DATABASE_URL`,
artifact store (`CONVOY_ARTIFACT_BUCKET`, endpoint/credentials), the payload
codec key (`CONVOY_CODEC_KEY_B64`), the LiteLLM proxy (`LITELLM_BASE_URL`,
`LITELLM_MASTER_KEY`), executor selection, sandbox provider selection
(`CONVOY_SANDBOX_PROVIDER`), and tuning knobs (turn limit, compaction
threshold). The Terraform module in `infra/` injects the same contract into
stack services.

## Deployment

`infra/` stamps a dedicated per-customer stack (VPC, RDS, S3, ECS services,
secrets) with one Terraform apply and documents the operator runbook: fresh
stamp, database initialization, LiteLLM model seeding, worker-versioned
deploys, and the manual AWS verification gates (including running the sandbox
contract suite against a stamped stack and verifying Temporal histories are
ciphertext-only).
