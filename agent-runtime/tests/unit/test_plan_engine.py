"""Revision validator unit tests, one section per invariant: immutable
history, graph validity (refs, cycles, shape, step cap), budget conservation,
fan-out structure, and the total-revision guarantees (version bump, purity,
approval scoping)."""

from decimal import Decimal
from typing import Any

from convoy_core import AgentSpec, ArtifactRef, Plan, PlanPatchOp, PlanStep, RunPolicy
from convoy_runtime.workflows.plan_engine import (
    requires_approval,
    validate_revision,
)

TEN = Decimal("10")


def _ref(key: str = "fixtures/plan.json") -> ArtifactRef:
    return ArtifactRef(bucket="b", key=key, size_bytes=1, sha256="x")


def _step(step_id: str, **overrides: Any) -> PlanStep:
    return PlanStep.model_validate({"id": step_id, "description": f"step {step_id}", **overrides})


def _subagent(agent_id: str) -> AgentSpec:
    return AgentSpec(
        id=agent_id, layer=1, max_children=0, model="scripted-echo-1", tools=[], prompt_ref=_ref()
    )


def _plan(*steps: PlanStep, version: int = 1) -> Plan:
    return Plan(version=version, goal="g", success_criteria=[], steps=list(steps), revisions=[])


def _linear_plan() -> Plan:
    return _plan(
        _step("s1", status="done"),
        _step("s2", status="running", depends_on=["s1"]),
        _step("s3", status="pending", depends_on=["s2"]),
    )


def _validate(
    plan: Plan,
    ops: list[PlanPatchOp],
    *,
    policy: RunPolicy | None = None,
    remaining: Decimal = TEN,
    max_children: int = 5,
):
    return validate_revision(
        plan,
        ops,
        policy=policy or RunPolicy(require_plan_approval=False),
        budget_remaining=remaining,
        max_children=max_children,
    )


def _add(step: PlanStep, after: str | None = None) -> PlanPatchOp:
    return PlanPatchOp(op="add_step", step=step, after=after, reason="test")


def _edit(step_id: str, changes: dict[str, Any]) -> PlanPatchOp:
    return PlanPatchOp(op="edit_step", step_id=step_id, changes=changes, reason="test")


# ------------------------------------------------- rule: history is immutable


def test_editing_a_done_step_is_rejected() -> None:
    result = _validate(_linear_plan(), [_edit("s1", {"description": "rewrite"})])
    assert not result.ok
    assert any("cannot edit step 's1'" in e for e in result.errors)


def test_editing_a_running_step_is_rejected() -> None:
    result = _validate(_linear_plan(), [_edit("s2", {"description": "rewrite"})])
    assert not result.ok


def test_editing_pending_ready_failed_blocked_steps_is_allowed() -> None:
    for status in ("pending", "ready", "failed", "blocked_on_human"):
        plan = _plan(_step("a", status=status))
        result = _validate(plan, [_edit("a", {"description": "new"})])
        assert result.ok, (status, result.errors)
        assert result.candidate is not None
        assert result.candidate.steps[0].description == "new"


def test_skipping_is_limited_to_pending_and_ready() -> None:
    for status, ok in (("pending", True), ("ready", True), ("running", False), ("done", False)):
        plan = _plan(_step("a", status=status))
        result = _validate(plan, [PlanPatchOp(op="skip_step", step_id="a", reason="test")])
        assert result.ok is ok, (status, result.errors)


def test_retry_reopens_only_failed_steps() -> None:
    ok_result = _validate(
        _plan(_step("a", status="failed")),
        [PlanPatchOp(op="retry_step", step_id="a", reason="test")],
    )
    assert ok_result.ok and ok_result.candidate is not None
    assert ok_result.candidate.steps[0].status == "ready"

    bad = _validate(
        _plan(_step("a", status="ready")),
        [PlanPatchOp(op="retry_step", step_id="a", reason="test")],
    )
    assert not bad.ok


def test_edit_may_not_touch_identity_or_execution_bookkeeping() -> None:
    cases: list[tuple[str, Any]] = [("id", "zz"), ("status", "done"), ("attempt", 3)]
    for field, value in cases:
        result = _validate(_plan(_step("a")), [_edit("a", {field: value})])
        assert not result.ok, field
        assert any("may not change" in e for e in result.errors)


# ---------------------------------------------------- rule: graph validity


def test_unknown_dependency_is_rejected() -> None:
    result = _validate(_plan(_step("a")), [_add(_step("b", depends_on=["ghost"]))])
    assert not result.ok
    assert any("unknown step 'ghost'" in e for e in result.errors)


def test_cycle_is_rejected() -> None:
    plan = _plan(_step("a"), _step("b", depends_on=["a"]))
    result = _validate(plan, [_edit("a", {"depends_on": ["b"]})])
    assert not result.ok
    assert any("cycle" in e for e in result.errors)


def test_self_dependency_is_rejected() -> None:
    result = _validate(_plan(_step("a")), [_edit("a", {"depends_on": ["a"]})])
    assert not result.ok


def test_add_after_unknown_step_is_rejected() -> None:
    result = _validate(_plan(_step("a")), [_add(_step("b"), after="ghost")])
    assert not result.ok


def test_duplicate_step_id_is_rejected() -> None:
    result = _validate(_plan(_step("a")), [_add(_step("a"))])
    assert not result.ok


def test_added_steps_must_start_pending() -> None:
    result = _validate(_plan(_step("a")), [_add(_step("b", status="running"))])
    assert not result.ok


def test_max_steps_policy_cap_is_enforced() -> None:
    policy = RunPolicy(require_plan_approval=False, max_steps=2)
    plan = _plan(_step("a"), _step("b", depends_on=["a"]))
    result = _validate(plan, [_add(_step("c", depends_on=["b"]), after="b")], policy=policy)
    assert not result.ok
    assert any("policy allows 2" in e for e in result.errors)


def test_linear_shape_rejects_branching() -> None:
    policy = RunPolicy(require_plan_approval=False, plan_shape="linear")
    plan = _plan(_step("a"), _step("b", depends_on=["a"]))
    result = _validate(plan, [_add(_step("c", depends_on=["a"]))], policy=policy)
    assert not result.ok
    assert any("chain" in e for e in result.errors)


def test_linear_fanout_rejects_branching_the_chain() -> None:
    plan = _plan(_step("a"), _step("b", depends_on=["a"]))
    result = _validate(plan, [_add(_step("c", depends_on=["a"]))])  # default linear_fanout
    assert not result.ok


def test_dag_shape_allows_branching() -> None:
    policy = RunPolicy(require_plan_approval=False, plan_shape="dag")
    plan = _plan(_step("a"), _step("b", depends_on=["a"]))
    result = _validate(plan, [_add(_step("c", depends_on=["a"]))], policy=policy)
    assert result.ok, result.errors


def test_appending_to_the_chain_end_is_linear_fanout_valid() -> None:
    plan = _plan(_step("a", status="done"), _step("b", status="running", depends_on=["a"]))
    result = _validate(plan, [_add(_step("c", depends_on=["b"]), after="b")])
    assert result.ok, result.errors
    assert result.candidate is not None
    assert [s.id for s in result.candidate.steps] == ["a", "b", "c"]


# ------------------------------------------------ rule: budget conservation


def test_budget_slices_of_active_steps_must_fit_remaining() -> None:
    plan = _plan(_step("a", status="ready", budget_slice="3"))
    over = _validate(
        plan,
        [_add(_step("b", depends_on=["a"], budget_slice="2"), after="a")],
        remaining=Decimal("4"),
    )
    assert not over.ok
    assert any("exceeding" in e for e in over.errors)

    exact = _validate(
        plan,
        [_add(_step("b", depends_on=["a"], budget_slice="1"), after="a")],
        remaining=Decimal("4"),
    )
    assert exact.ok, exact.errors


def test_terminal_step_slices_do_not_count_against_budget() -> None:
    plan = _plan(
        _step("a", status="done", budget_slice="100"),
        _step("z", status="skipped", budget_slice="50", depends_on=["a"]),
        _step("b", status="ready", depends_on=["z"]),
    )
    result = _validate(
        plan,
        [
            PlanPatchOp(
                op="set_budget_slice",
                step_id="b",
                changes={"budget_slice": "4"},
                reason="test",
            )
        ],
        remaining=Decimal("4"),
    )
    assert result.ok, result.errors
    assert result.candidate is not None
    assert result.candidate.steps[2].budget_slice == Decimal("4")


def test_set_budget_slice_requires_exactly_that_change() -> None:
    plan = _plan(_step("a"))
    result = _validate(
        plan,
        [
            PlanPatchOp(
                op="set_budget_slice",
                step_id="a",
                changes={"budget_slice": "1", "description": "x"},
                reason="test",
            )
        ],
    )
    assert not result.ok


# ---------------------------------------------------- rule: fan-out structure


def _group_plan(*, member_deps: list[list[str]] | None = None, with_join: bool = True) -> Plan:
    deps = member_deps or [["a"], ["a"]]
    members = [
        _step(
            f"m{i}",
            group_id="g1",
            executor="subagent",
            subagent=_subagent(f"child-{i}").model_dump(),
            depends_on=dep,
        )
        for i, dep in enumerate(deps)
    ]
    steps = [_step("a", status="done"), *members]
    if with_join:
        steps.append(_step("join", depends_on=[m.id for m in members]))
    return _plan(*steps)


def _noop_op() -> PlanPatchOp:
    # A benign op so whole-plan checks run against the existing structure.
    return PlanPatchOp(
        op="set_budget_slice", step_id="a", changes={"budget_slice": None}, reason="test"
    )


def test_valid_fanout_group_is_accepted() -> None:
    plan = _group_plan()
    plan.steps[0].status = "pending"  # keep the slice target editable
    result = _validate(plan, [_noop_op()])
    assert result.ok, result.errors


def test_group_exceeding_max_children_is_rejected() -> None:
    plan = _group_plan(member_deps=[["a"], ["a"], ["a"]])
    plan.steps[0].status = "pending"
    result = _validate(plan, [_noop_op()], max_children=2)
    assert not result.ok
    assert any("max_children" in e for e in result.errors)


def test_group_members_must_share_dependencies() -> None:
    plan = _group_plan(member_deps=[["a"], []])
    plan.steps[0].status = "pending"
    result = _validate(plan, [_noop_op()])
    assert not result.ok
    assert any("share dependencies" in e for e in result.errors)


def test_group_members_must_be_subagent_steps() -> None:
    plan = _group_plan()
    plan.steps[0].status = "pending"
    plan.steps[1].executor = "self"
    plan.steps[1].subagent = None
    result = _validate(plan, [_noop_op()])
    assert not result.ok
    assert any("subagent" in e for e in result.errors)


def test_group_without_a_join_step_is_rejected() -> None:
    plan = _group_plan(with_join=False)
    plan.steps[0].status = "pending"
    result = _validate(plan, [_noop_op()])
    assert not result.ok
    assert any("join step" in e for e in result.errors)


# ------------------------------------------------- rule: total revisions


def test_version_bumps_by_exactly_one_and_input_is_unmutated() -> None:
    plan = _linear_plan()
    before = plan.model_dump()
    result = _validate(plan, [_add(_step("s4", depends_on=["s3"]), after="s3")])
    assert result.ok and result.candidate is not None
    assert result.candidate.version == plan.version + 1
    assert plan.model_dump() == before  # single writer: validation never mutates
    assert len(result.candidate.steps) == 4


def test_empty_revision_is_rejected() -> None:
    result = _validate(_linear_plan(), [])
    assert not result.ok
    assert any("at least one operation" in e for e in result.errors)


def test_ops_apply_in_order_and_may_reference_new_steps() -> None:
    plan = _plan(_step("a"))
    result = _validate(
        plan,
        [
            _add(_step("b", depends_on=["a"]), after="a"),
            _edit("b", {"description": "edited new step"}),
        ],
    )
    assert result.ok, result.errors
    assert result.candidate is not None
    assert result.candidate.steps[1].description == "edited new step"


def test_requires_approval_matrix() -> None:
    add = [_add(_step("x"))]
    edit = [_edit("a", {"description": "y"})]
    minor = [PlanPatchOp(op="skip_step", step_id="a", reason="test")]

    off = RunPolicy(require_plan_approval=False)
    assert requires_approval(add, off) is False
    assert requires_approval(minor, off) is False

    initial = RunPolicy(require_plan_approval=True, approval_scope="initial")
    assert requires_approval(add, initial) is False
    assert requires_approval(minor, initial) is False

    major = RunPolicy(require_plan_approval=True, approval_scope="major_revisions")
    assert requires_approval(add, major) is True
    assert requires_approval(edit, major) is True
    assert requires_approval(minor, major) is False
    assert (
        requires_approval([PlanPatchOp(op="retry_step", step_id="a", reason="test"), *add], major)
        is True
    )

    everything = RunPolicy(require_plan_approval=True, approval_scope="all_revisions")
    assert requires_approval(minor, everything) is True
    assert requires_approval(add, everything) is True
