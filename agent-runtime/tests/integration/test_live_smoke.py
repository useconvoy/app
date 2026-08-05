"""Live-smoke lane: one linear run through the LiteLLM proxy against a REAL
model, using a provider key from the operator's environment.

Opt-in only — `make live-smoke` — and never part of the deterministic lanes.
Without a key every test here skips with a clear reason, so accidental
collection is CI-safe. With a key it proves the production model path end to
end: the run completes, the workflow's budget accounting records real
proxy-priced spend, and `TurnResult.model_used` lands in projections.

Default route: `live-anthropic` (claude-sonnet-5) when ANTHROPIC_API_KEY is
set, `live-openai` when only OPENAI_API_KEY is; CONVOY_LIVE_SMOKE_MODEL picks
any other route the proxy serves.
"""

import os
import uuid
from decimal import Decimal

import httpx
import pytest
from _support.e2e import auth_headers, collect_sse, live_smoke_model

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        live_smoke_model() is None,
        reason=(
            "live-smoke needs a real model provider key: set ANTHROPIC_API_KEY "
            "(default model claude-sonnet-5) or OPENAI_API_KEY, then run `make live-smoke`"
        ),
    ),
]


def test_linear_run_lands_on_a_real_model(api_live: httpx.Client, live_stack: str) -> None:
    model = live_stack
    run_id = f"run-live-smoke-{uuid.uuid4().hex[:8]}"
    created = api_live.post(
        "/runs",
        json={
            "goal": (
                "Live smoke test: no investigation is needed. For every step, "
                "reply with one short sentence confirming the step is complete."
            ),
            "success_criteria": ["run lands on a real model"],
            "run_id": run_id,
            "model": model,
            # Real dollars: keep the cap tight so a misbehaving run stops.
            "budget_usd": "1.00",
        },
        headers=auth_headers(),
    )
    assert created.status_code == 202, created.text

    events = collect_sse(api_live, run_id, terminal={"run_completed", "run_failed"}, timeout=300.0)
    assert events[-1]["type"] == "run_completed", [e["type"] for e in events]

    step_done = [e for e in events if e["type"] == "step_done"]
    assert step_done, "run completed without any step_done events"
    for event in step_done:
        # The model that actually served the turn is recorded, and it is the
        # real one — not a fallback, not the mock.
        assert event["payload"]["model_used"] == model
        assert event["payload"]["model_fallback"] is False

    # Real nonzero spend, priced by the proxy, accumulated by the workflow.
    spent = Decimal(step_done[-1]["payload"]["budget"]["spent_usd"])
    assert spent > 0, "budget accounting recorded zero cost for a real model run"

    run = api_live.get(f"/runs/{run_id}", headers=auth_headers()).json()
    assert run["status"] == "completed"
    assert all(step["model_used"] == model for step in run["steps"])
    assert Decimal(run["budget"]["spent_usd"]) > 0


def test_skip_proof_helper_reports_available_key() -> None:
    """Sanity on the opt-in wiring itself: when this test runs, a key was
    present and the resolved route matches it."""
    model = live_smoke_model()
    assert model is not None
    if os.environ.get("CONVOY_LIVE_SMOKE_MODEL"):
        assert model == os.environ["CONVOY_LIVE_SMOKE_MODEL"]
    elif os.environ.get("ANTHROPIC_API_KEY"):
        assert model == "live-anthropic"
    else:
        assert model == "live-openai"
