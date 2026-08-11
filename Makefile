COMPOSE := docker compose -f agent-runtime/compose.yaml

.PHONY: lint fmt typecheck test e2e e2e-up e2e-down chaos live-smoke record-history sandbox-up sandbox-down sandbox-test

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
	uv run pytest agent-runtime/tests -m "not e2e and not chaos and not live"

e2e-up:
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

# Chaos lane: crash/durability suite (worker kills, sandbox loss) against
# the same compose stack, kept out of e2e so that lane stays fast.
chaos: e2e-up
	uv run pytest agent-runtime/tests -m chaos; \
	status=$$?; \
	$(COMPOSE) down -v; \
	exit $$status

# Live-smoke lane (opt-in, never CI-default): one linear run through the
# LiteLLM proxy against a real model. Needs ANTHROPIC_API_KEY (default model
# claude-sonnet-5) or OPENAI_API_KEY; without a key the tests skip cleanly
# and no containers are started.
live-smoke:
	@if [ -z "$$ANTHROPIC_API_KEY" ] && [ -z "$$OPENAI_API_KEY" ]; then \
		uv run pytest agent-runtime/tests -m live; \
	else \
		$(MAKE) e2e-up && uv run pytest agent-runtime/tests -m live; \
		status=$$?; \
		$(COMPOSE) down -v; \
		exit $$status; \
	fi

# Re-record checked-in replay histories. Requires a rationale in the commit
# message; prefer workflow.patched versioning for live-run compatibility.
record-history:
	uv run python agent-runtime/tests/histories/record.py

# Provider-shaped connector stubs. These exercise the production Slack,
# Google, and GitHub connector implementations against fake local data.
sandbox-up:
	docker compose -f sandbox/compose.yaml up -d --build --wait

sandbox-down:
	docker compose -f sandbox/compose.yaml down

sandbox-test:
	cd sandbox && npm test
