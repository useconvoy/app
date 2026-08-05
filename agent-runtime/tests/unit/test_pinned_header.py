"""Pinned-header assembly golden test: fixture state in, exact header out.

The header is rebuilt from `RunState` before every turn; its exact shape is
part of the turn contract, so it is pinned against a golden file next to this
test."""

import json
from decimal import Decimal
from pathlib import Path

from _support.common import fixture_ref, fixture_run_state

from convoy_core import PlanStep, SteerMessage
from convoy_runtime.activities.plan import build_fixture_plan
from convoy_runtime.providers.context_assembly import build_pinned_header, render_pinned_header

GOLDEN_PATH = Path(__file__).with_name("golden_pinned_header.json")


def _state_with_progress():
    state = fixture_run_state(run_id="run-golden-1", budget_cap_usd=Decimal("2.50"))
    plan = build_fixture_plan("map the Q3 numbers", ["report lands"], fixture_ref("plans/v1.json"))
    plan.steps[0] = PlanStep.model_validate(
        {**plan.steps[0].model_dump(), "status": "done", "attempt": 1}
    )
    plan.steps[1] = PlanStep.model_validate(
        {**plan.steps[1].model_dump(), "status": "running", "attempt": 1}
    )
    state.plan = plan
    state.turn_count = 1
    state.budget.spent_usd = Decimal("1.25")
    state.pending_steers = [
        SteerMessage(
            id="steer-1",
            author="human",
            author_id="ana@example.test",
            mode="note",
            body="prefer the finance ledger",
        )
    ]
    return state


def test_pinned_header_matches_golden() -> None:
    header = build_pinned_header(_state_with_progress())
    golden = json.loads(GOLDEN_PATH.read_text())
    assert header == golden


def test_budget_fraction_is_stable_and_quantized() -> None:
    header = build_pinned_header(_state_with_progress())
    assert header["budget"]["spent_fraction"] == "0.5000"
    assert header["budget"]["remaining_usd"] == "1.25"


def test_zero_cap_produces_no_fraction() -> None:
    state = fixture_run_state(run_id="run-golden-2", budget_cap_usd=Decimal("0"))
    header = build_pinned_header(state)
    assert header["budget"]["spent_fraction"] is None


def test_render_contains_every_section() -> None:
    text = render_pinned_header(build_pinned_header(_state_with_progress()))
    assert "Goal: map the Q3 numbers" in text
    assert "- report lands" in text
    assert "[done] step-1" in text
    assert "[running] step-2" in text
    assert "Budget: spent 1.25 of 2.50 USD" in text
    assert "(note) ana@example.test: prefer the finance ledger" in text
