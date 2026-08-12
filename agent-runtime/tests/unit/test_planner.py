"""Planner tests: instruction parsing, plan assembly, the model planner's
HTTP contract, and create_plan's selection order (fixture affordances win,
then instructions, then the model with fixture fallback)."""

import hashlib
import json
from typing import Any

import httpx
import pytest

from convoy_core import ArtifactRef, HumanGate, RunState
from convoy_runtime.activities.plan import PlanActivities
from convoy_runtime.providers.planner import (
    ModelPlanner,
    PlannedStep,
    PlannerError,
    assemble_plan,
    build_instruction_plan,
    parse_instructions,
)

pytestmark = pytest.mark.anyio


def _ref(key: str = "runs/run-x/pinned.json") -> ArtifactRef:
    data = b"planner-fixture"
    return ArtifactRef(
        bucket="convoy-test", key=key, size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest()
    )


# ---------------------------------------------------------------- parsing


def test_instruction_lines_become_ordered_steps() -> None:
    parsed = parse_instructions(["Read the tracker", "  ", "Draft the digest"])
    assert [step.description for step in parsed] == ["Read the tracker", "Draft the digest"]
    assert all(step.checkpoint is None for step in parsed)


def test_checkpoint_line_gates_the_next_step() -> None:
    parsed = parse_instructions(
        ["Draft the digest", "checkpoint: Approve the draft", "Post to the channel"]
    )
    assert [step.description for step in parsed] == ["Draft the digest", "Post to the channel"]
    assert parsed[0].checkpoint is None
    assert parsed[1].checkpoint == "Approve the draft"


def test_consecutive_checkpoints_merge_and_trailing_checkpoint_gates_the_last_step() -> None:
    parsed = parse_instructions(
        [
            "Checkpoint: First question",
            "checkpoint: Second question",
            "Do the work",
            "checkpoint: Final sign-off",
        ]
    )
    assert len(parsed) == 1
    assert parsed[0].checkpoint == "First question; Second question; Final sign-off"


def test_bare_checkpoint_gets_a_default_prompt() -> None:
    parsed = parse_instructions(["checkpoint:", "Send it"])
    assert parsed[0].checkpoint == "Approve before continuing."


def test_only_checkpoints_yield_no_steps() -> None:
    assert parse_instructions(["checkpoint: nothing to do"]) == []


# ---------------------------------------------------------------- assembly


def test_instruction_plan_is_linear_with_gates_attached() -> None:
    plan = build_instruction_plan(
        "Post the digest",
        ["The digest lands in the channel"],
        ["Read messages", "checkpoint: OK to post?", "Post the digest"],
        _ref(),
    )
    assert [step.id for step in plan.steps] == ["step-1", "step-2"]
    assert plan.steps[0].status == "ready"
    assert plan.steps[0].depends_on == []
    assert plan.steps[1].status == "pending"
    assert plan.steps[1].depends_on == ["step-1"]
    gate = plan.steps[1].human_gate
    assert isinstance(gate, HumanGate)
    assert gate.kind == "approval"
    assert gate.prompt == "OK to post?"
    assert plan.version == 1
    assert plan.revisions[0].reason == "initial"
    assert len(plan.revisions[0].ops) == 2


def test_instruction_plan_refuses_empty_instructions() -> None:
    with pytest.raises(ValueError):
        build_instruction_plan("goal", [], ["   ", ""], _ref())


def test_assemble_plan_honors_optional_steps() -> None:
    plan = assemble_plan(
        "goal",
        [],
        [PlannedStep(description="Must"), PlannedStep(description="Nice", required=False)],
        _ref(),
    )
    assert plan.steps[0].required is True
    assert plan.steps[1].required is False


# ------------------------------------------------------------ model planner


def _completion(content: str) -> dict[str, Any]:
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def _planner(handler: Any) -> ModelPlanner:
    return ModelPlanner(
        base_url="http://litellm.test",
        api_key="master",
        model="claude-x",
        transport=httpx.MockTransport(handler),
    )


async def test_model_planner_parses_structured_steps() -> None:
    body = json.dumps(
        {
            "steps": [
                {"description": "Read the sheet", "checkpoint": None, "required": True},
                {"description": "Post the summary", "checkpoint": "Send it?", "required": True},
            ]
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer master"
        payload = json.loads(request.content)
        assert payload["model"] == "claude-x"
        assert "Goal: Ship the digest" in payload["messages"][1]["content"]
        return httpx.Response(200, json=_completion(body))

    steps = await _planner(handler).plan("Ship the digest", [], ["slack.post_message"])
    assert [step.description for step in steps] == ["Read the sheet", "Post the summary"]
    assert steps[1].checkpoint == "Send it?"


async def test_model_planner_tolerates_markdown_fences() -> None:
    fenced = '```json\n{"steps": [{"description": "Only step"}]}\n```'

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion(fenced))

    steps = await _planner(handler).plan("goal", [], [])
    assert [step.description for step in steps] == ["Only step"]


async def test_model_planner_raises_on_unparseable_content() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion("I would suggest three steps..."))

    with pytest.raises(PlannerError):
        await _planner(handler).plan("goal", [], [])


async def test_model_planner_raises_on_http_failure() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream down")

    with pytest.raises(PlannerError):
        await _planner(handler).plan("goal", [], [])


# ------------------------------------------------- create_plan selection


class MemoryStore:
    """Just enough ArtifactStore for the plan activity: pinned payload in,
    snapshot out."""

    bucket = "convoy-test"

    def __init__(self, pinned: dict[str, Any]) -> None:
        self._pinned = pinned
        self.snapshots: dict[str, Any] = {}

    async def get_json(self, _ref: ArtifactRef) -> dict[str, Any]:
        return self._pinned

    async def put_json(self, key: str, payload: dict[str, Any]) -> ArtifactRef:
        self.snapshots[key] = payload
        data = json.dumps(payload).encode()
        return ArtifactRef(
            bucket=self.bucket,
            key=key,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )


def _state(run_id: str = "run-plan-1") -> RunState:
    from _support.common import fixture_run_state

    return fixture_run_state(run_id=run_id)


async def test_create_plan_prefers_instructions_over_the_model(monkeypatch: Any) -> None:
    store = MemoryStore(
        {"goal": "Post it", "success_criteria": [], "instructions": ["Read", "Post"]}
    )

    class ExplodingPlanner:
        async def plan(self, *_args: Any) -> Any:
            raise AssertionError("the model must not be consulted when instructions exist")

    activities = PlanActivities(store, ExplodingPlanner())  # type: ignore[arg-type]
    plan = await activities.create_plan(_state())
    assert [step.description for step in plan.steps] == ["Read", "Post"]
    assert "runs/run-plan-1/plans/v1.json" in store.snapshots


async def test_create_plan_keeps_fixture_path_when_fixture_gates_ride_along() -> None:
    store = MemoryStore(
        {
            "goal": "Gated goal",
            "success_criteria": [],
            "instructions": ["ignored by the fixture path"],
            "fixture_gates": {
                "step-2": {"kind": "approval", "prompt": "Proceed?", "on_timeout": "pause"}
            },
        }
    )
    activities = PlanActivities(store)
    plan = await activities.create_plan(_state("run-plan-2"))
    assert [step.id for step in plan.steps] == ["step-1", "step-2"]
    assert plan.steps[1].human_gate is not None
    assert plan.steps[1].human_gate.prompt == "Proceed?"


async def test_create_plan_uses_the_model_and_falls_back_on_planner_error() -> None:
    store = MemoryStore({"goal": "Model goal", "success_criteria": []})

    class GoodPlanner:
        async def plan(self, goal: str, _criteria: Any, _tools: Any) -> list[PlannedStep]:
            assert goal == "Model goal"
            return [PlannedStep(description="Model step")]

    plan = await PlanActivities(store, GoodPlanner()).create_plan(_state("run-plan-3"))  # type: ignore[arg-type]
    assert [step.description for step in plan.steps] == ["Model step"]

    class BadPlanner:
        async def plan(self, *_args: Any) -> Any:
            raise PlannerError("model unavailable")

    fallback_store = MemoryStore({"goal": "Model goal", "success_criteria": []})
    plan = await PlanActivities(fallback_store, BadPlanner()).create_plan(  # type: ignore[arg-type]
        _state("run-plan-4")
    )
    assert [step.id for step in plan.steps] == ["step-1", "step-2"]
    assert plan.steps[0].description.startswith("Investigate:")
