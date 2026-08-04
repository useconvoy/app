"""Subagent result distillation: the compaction contract stays small and
typed — bounded headline/summary, findings from outputs, refs only."""

from decimal import Decimal

from _support.common import fixture_ref

from convoy_core import AgentSpec, TokenCounts
from convoy_runtime.activities.subagent import build_subagent_header, build_subagent_result
from convoy_runtime.workflows.subagent import SubagentBrief, SubagentWrapUp


def _brief(goal: str = "count the beans") -> SubagentBrief:
    return SubagentBrief(
        run_id="run-p--fan-1-a1",
        parent_run_id="run-p",
        tenant_id="tenant-test",
        environment_id="stub-local",
        binding_ref=fixture_ref("runs/run-p/binding.json"),
        step_id="fan-1",
        group_id="fanout-1",
        goal=goal,
        input_refs=[fixture_ref("runs/run-p/outputs/step-1.json")],
        agent=AgentSpec(
            id="run-p-root-fan-1",
            layer=1,
            parent_id="run-p-root",
            max_children=0,
            model="scripted-echo-1",
            tools=[],
            prompt_ref=fixture_ref("runs/run-p/prompts/root.json"),
        ),
        spawned_by="run-p-root",
        budget_cap_usd=Decimal("1.00"),
    )


def _wrapup(status: str = "done", error: str | None = None, outputs: int = 2) -> SubagentWrapUp:
    return SubagentWrapUp.model_validate(
        {
            "brief": _brief(),
            "status": status,
            "transcript_ref": fixture_ref("runs/run-p--fan-1-a1/transcripts/fan-1/turn-1.json"),
            "outputs": [
                fixture_ref(f"runs/run-p--fan-1-a1/outputs/part-{i}.json") for i in range(outputs)
            ],
            "cost_usd": Decimal("0.25"),
            "tokens": TokenCounts(input_tokens=24, output_tokens=14),
            "turns": 3,
            "error": error,
        }
    )


def test_done_result_distills_headline_summary_and_findings() -> None:
    wrapup = _wrapup()
    assert wrapup.transcript_ref is not None
    result = build_subagent_result(wrapup, wrapup.transcript_ref, None)
    assert result.subagent_id == "run-p-root-fan-1"
    assert result.step_id == "fan-1"
    assert result.status == "done"
    assert result.headline == "Completed: count the beans"
    assert "3 turn(s)" in result.summary
    assert result.cost_usd == Decimal("0.25")
    assert result.tokens.input_tokens == 24
    assert [f.key for f in result.findings] == ["output:1", "output:2"]
    assert all(f.refs for f in result.findings)
    assert result.outputs == wrapup.outputs
    assert result.open_questions == []
    assert result.error_ref is None


def test_failed_result_carries_error_and_open_question() -> None:
    wrapup = _wrapup(status="failed", error="turn 3 failed", outputs=0)
    assert wrapup.transcript_ref is not None
    error_ref = fixture_ref("runs/run-p--fan-1-a1/errors/wrapup.json")
    result = build_subagent_result(wrapup, wrapup.transcript_ref, error_ref)
    assert result.status == "failed"
    assert result.headline.startswith("Failed:")
    assert "turn 3 failed" in result.summary
    assert result.error_ref == error_ref
    assert result.open_questions and "did not finish" in result.open_questions[0]


def test_landed_partial_result_flags_the_open_goal() -> None:
    wrapup = _wrapup(status="landed_partial")
    assert wrapup.transcript_ref is not None
    result = build_subagent_result(wrapup, wrapup.transcript_ref, None)
    assert result.headline.startswith("Landed with partial results:")
    assert result.open_questions and "landed before its goal" in result.open_questions[0]


def test_summary_stays_bounded_for_absurd_goals() -> None:
    brief = _brief(goal="x" * 5000)
    wrapup = SubagentWrapUp.model_validate({**_wrapup().model_dump(), "brief": brief})
    assert wrapup.transcript_ref is not None
    result = build_subagent_result(wrapup, wrapup.transcript_ref, None)
    assert len(result.headline) <= 200
    assert len(result.summary) <= 1000  # the compaction contract stays ~1 KB


def test_subagent_header_carries_scoped_goal_inputs_and_slice() -> None:
    header = build_subagent_header(_brief())
    assert header["goal"] == "count the beans"
    assert header["parent_run_id"] == "run-p"
    assert header["budget"] == {"cap_usd": "1.00"}
    assert header["layer"] == 1
    assert [ref["key"] for ref in header["inputs"]] == ["runs/run-p/outputs/step-1.json"]
