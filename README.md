# Convoy Labs

Platform for long-running, specialized agents that execute routine work in sandboxed and production environments.

## Repository Structure

- `website/` — Web app: environment setup, agent configuration, run console, and telemetry dashboards.
- `agent-runtime/` — Durable agent orchestration: agent loop, plans, budgets, pause/steer, and subagent coordination.
- `environments/` — Sandbox and production environments: tool connections, permissions, and compute selection.
- `agent-evals/` — Eval harness: task suites, run scoring, and benchmarks for specialized agents.
- `telemetry/` — Run observability: goal progress, cost tracking, and user feedback capture.
- `learning/` — Continuous improvement: feedback-driven memory, routine, and agent updates.
- `core/` — Shared datatypes (`convoy_core`): cross-service schemas, owned by no service.

## Development

The Python services form a [uv](https://docs.astral.sh/uv/) workspace rooted
here (`core/` and `agent-runtime/` are members; `uv sync` installs
everything). The root `Makefile` carries the day-to-day targets — `make lint`,
`make typecheck`, `make test`, and the container-backed `make e2e` /
`make chaos` lanes; see `agent-runtime/README.md` for what each lane covers
and `agent-runtime/infra/README.md` for stack deployment.
