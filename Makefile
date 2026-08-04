COMPOSE := docker compose -f agent-runtime/compose.yaml

.PHONY: lint fmt typecheck test e2e e2e-up e2e-down litellm-prefetch record-history

lint:
	uv run ruff format --check .
	uv run ruff check .

fmt:
	uv run ruff format .
	uv run ruff check --fix .

typecheck:
	uv run pyright

# Fast lane: unit + workflow + activity + contracts + scenarios + replay +
# lint tests. No containers, no network, no real model keys.
test:
	uv run pytest agent-runtime/tests -m "not e2e"

# The litellm image builds fully offline; its inputs (wheels, prisma engines)
# are prefetched on the host first. Idempotent.
litellm-prefetch:
	agent-runtime/docker/litellm/prefetch.sh

e2e-up: litellm-prefetch
	$(COMPOSE) up -d --build --wait

e2e-down:
	$(COMPOSE) down -v

# e2e lane: everything marked e2e against the compose stack (full runs over
# SSE + projections, executor contract suite vs mock-model, RLS checks).
e2e: e2e-up
	uv run pytest agent-runtime/tests -m e2e; \
	status=$$?; \
	$(COMPOSE) down -v; \
	exit $$status

# Re-record checked-in replay histories. Requires a rationale in the commit
# message; prefer workflow.patched versioning for live-run compatibility.
record-history:
	uv run python agent-runtime/tests/histories/record.py
