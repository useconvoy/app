# Convoy Labs

Platform for long-running, specialized agents that execute routine work in sandboxed and production environments.

## Repository Structure

- `website/` — Web app: environment setup, agent configuration, run console, and telemetry dashboards.
- `agent-runtime/` — Durable agent orchestration: agent loop, plans, budgets, pause/steer, and subagent coordination.
- `environments/` — Sandbox and production environments: tool connections, permissions, and compute selection.
- `agent-evals/` — Eval harness: task suites, run scoring, and benchmarks for specialized agents.
- `telemetry/` — Run observability: goal progress, cost tracking, and user feedback capture.
- `learning/` — Continuous improvement: feedback-driven memory, routine, and agent updates.
