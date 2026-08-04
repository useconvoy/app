"""Fixture plan validity: the create_plan stub must produce a well-formed
linear 2-step plan with a coherent initial revision, and — when asked — a
fan-out group that satisfies the revision validator's structural rules."""

from decimal import Decimal

from _support.common import fixture_ref

from convoy_core import AgentSpec, Plan, PlanPatchOp, RunPolicy
from convoy_runtime.activities.plan import FanoutFixture, build_fixture_plan
from convoy_runtime.workflows.plan_engine import validate_revision


def _plan() -> Plan:
    return build_fixture_plan("test goal", ["lands cleanly"], fixture_ref("plans/v1.json"))


def _root_agent() -> AgentSpec:
    return AgentSpec(
        id="run-x-root",
        layer=0,
        max_children=5,
        model="scripted-echo-1",
        tools=[],
        prompt_ref=fixture_ref("prompts/root.json"),
    )


def _fanout_plan(size: int = 3) -> Plan:
    return build_fixture_plan(
        "test goal",
        ["lands cleanly"],
        fixture_ref("plans/v1.json"),
        None,
        FanoutFixture(size=size, budget_slice=Decimal("1.00")),
        agent=_root_agent(),
    )


def test_fixture_plan_shape() -> None:
    plan = _plan()
    assert plan.version == 1
    assert plan.goal == "test goal"
    assert plan.success_criteria == ["lands cleanly"]
    assert len(plan.steps) == 2

    ids = [s.id for s in plan.steps]
    assert len(set(ids)) == len(ids)

    # Linear: step-1 has no deps and is ready; step-2 depends only on step-1.
    first, second = plan.steps
    assert first.depends_on == []
    assert first.status == "ready"
    assert second.depends_on == [first.id]
    assert second.status == "pending"
    assert all(s.executor == "self" for s in plan.steps)
    assert all(dep in ids for s in plan.steps for dep in s.depends_on)


def test_fixture_plan_initial_revision() -> None:
    plan = _plan()
    assert len(plan.revisions) == 1
    revision = plan.revisions[0]
    assert revision.version == 1
    assert revision.author == "system"
    assert revision.reason == "initial"
    assert [op.op for op in revision.ops] == ["add_step", "add_step"]
    assert all(op.step is not None for op in revision.ops)
    assert revision.snapshot_ref.key == "plans/v1.json"


def test_fixture_plan_serializes_round_trip() -> None:
    plan = _plan()
    assert Plan.model_validate_json(plan.model_dump_json()) == plan


def test_fanout_fixture_plan_shape() -> None:
    plan = _fanout_plan(size=3)
    members = [s for s in plan.steps if s.group_id == "fanout-1"]
    assert [m.id for m in members] == ["fan-1", "fan-2", "fan-3"]
    join = next(s for s in plan.steps if s.id == "step-2")
    assert sorted(join.depends_on) == ["fan-1", "fan-2", "fan-3"]
    assert join.required is True
    for index, member in enumerate(members, start=1):
        assert member.executor == "subagent"
        assert member.depends_on == ["step-1"]
        assert member.budget_slice == Decimal("1.00")
        # Members are individually optional so a policy-tolerated failure
        # still lands as a valid partial completion; the join is required.
        assert member.required is False
        spec = member.subagent
        assert spec is not None
        assert spec.layer == 1
        assert spec.parent_id == "run-x-root"
        assert spec.id == f"run-x-root-fan-{index}"


def test_fanout_fixture_plan_passes_the_revision_validator() -> None:
    # The structural rules the validator applies to any revised plan (shape,
    # fan-out membership, join step, budget slices) hold for the fixture's
    # initial form: a benign no-op revision over it validates cleanly.
    plan = _fanout_plan(size=2)
    benign = validate_revision(
        plan,
        [
            PlanPatchOp(
                op="set_budget_slice",
                step_id="fan-1",
                changes={"budget_slice": "1.00"},
                reason="fixture structural check",
            )
        ],
        policy=RunPolicy(require_plan_approval=False),
        budget_remaining=Decimal("10"),
        max_children=5,
    )
    assert benign.ok, benign.errors
