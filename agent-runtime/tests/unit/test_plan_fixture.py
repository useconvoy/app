"""Fixture plan validity (TESTING §5-M0): the M0 create_plan stub must produce
a well-formed linear 2-step plan with a coherent initial revision."""

from _support.common import fixture_ref

from convoy_core import Plan
from convoy_runtime.activities.plan import build_fixture_plan


def _plan() -> Plan:
    return build_fixture_plan("test goal", ["lands cleanly"], fixture_ref("plans/v1.json"))


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
