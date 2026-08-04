COMPOSE := docker compose -f agent-runtime/compose.yaml

.PHONY: lint fmt typecheck test e2e e2e-up e2e-down record-history

lint:
	uv run ruff format --check .
	uv run ruff check .

fmt:
	uv run ruff format .
	uv run ruff check --fix .

typecheck:
	uv run pyright

# Fast lane (TESTING.md section 4.5): unit + workflow + activity + contracts +
# replay + lint tests. No containers, no network, no real model keys.
test:
	uv run pytest agent-runtime/tests -m "not e2e"

e2e-up:
	$(COMPOSE) up -d --build --wait

e2e-down:
	$(COMPOSE) down -v

# e2e lane: full flow against compose (POST /runs -> SSE -> land -> projections).
e2e: e2e-up
	uv run pytest agent-runtime/tests/integration -m e2e; \
	status=$$?; \
	$(COMPOSE) down -v; \
	exit $$status

# Re-record checked-in replay histories (TESTING.md section 4.2). Requires a
# rationale in the commit message.
record-history:
	uv run python agent-runtime/tests/histories/record.py
