"""Plan revision engine — pure, deterministic validation and application.

Activities and agents only ever *propose* plan changes as constrained patch
ops; the workflow is the single writer that validates and applies them. This
module is that guard. Everything here is pure computation on plan values so
it is safe inside workflow code.

A proposal is checked against the live plan before anything mutates:

1. Single writer — enforced structurally: only workflow code calls
   `validate_revision` and commits its candidate; everything else returns
   proposals.
2. History is immutable — `done` and `running` steps cannot be touched;
   edits target pending/ready/failed/blocked steps only.
3. Graph validity — every reference resolves, the dependency graph is
   acyclic, the configured plan shape holds, and the step count stays
   within policy.
4. Budget conservation — the budget slices of steps that can still consume
   money never exceed the budget remaining right now.
5. Fan-out rules — group members share their dependencies, are subagent
   steps with specs, stay within the child cap, and a join step depends on
   every member.
6. Total revisions — an applied revision bumps the version by exactly one;
   the caller archives a snapshot and records author + reason; major ops
   (add_step, edit_step) require approval per policy while minor ops don't.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, cast

from convoy_core import Plan, PlanPatchOp, PlanStep, RunPolicy

# Ops that restructure the plan and therefore need human approval under the
# default approval scope; the remaining ops are corrective bookkeeping.
MAJOR_OPS = frozenset({"add_step", "edit_step"})

# Steps in these statuses may still consume budget, so their slices count
# against what is left.
_BUDGET_ACTIVE_STATUSES = frozenset({"pending", "ready", "running", "blocked_on_human"})

# Step statuses whose steps a revision may edit; done and running are
# immutable history, skipped is terminal.
_EDITABLE_STATUSES = frozenset({"pending", "ready", "failed", "blocked_on_human"})
_SKIPPABLE_STATUSES = frozenset({"pending", "ready"})

# Fields edit_step may change. Identity and execution bookkeeping (id,
# status, attempt, outputs, error_ref) belong to the run machinery, never to
# a proposal.
_EDITABLE_FIELDS = frozenset(
    {
        "description",
        "depends_on",
        "group_id",
        "executor",
        "subagent",
        "budget_slice",
        "human_gate",
        "eval_gate",
        "max_attempts",
        "required",
    }
)


@dataclass(frozen=True)
class RevisionValidation:
    """Outcome of validating a proposal: either reasons it is invalid, or
    the candidate plan (version already bumped by one) ready to commit."""

    errors: list[str]
    candidate: Plan | None

    @property
    def ok(self) -> bool:
        return not self.errors


def requires_approval(ops: list[PlanPatchOp], policy: RunPolicy) -> bool:
    """Whether an (already validated) revision must wait for human approval.

    The approval scope narrows which revisions re-enter approval: "initial"
    means only the very first plan is ever approved, "major_revisions" gates
    structural ops, "all_revisions" gates everything.
    """
    if not policy.require_plan_approval:
        return False
    if policy.approval_scope == "initial":
        return False
    if policy.approval_scope == "all_revisions":
        return True
    return any(op.op in MAJOR_OPS for op in ops)


def validate_revision(
    plan: Plan,
    ops: list[PlanPatchOp],
    *,
    policy: RunPolicy,
    budget_remaining: Decimal,
    max_children: int,
) -> RevisionValidation:
    """Validate a proposed revision against the live plan.

    Op-level checks run while the ops are applied in order to a working
    copy (so later ops may reference steps added by earlier ones); whole-plan
    checks (graph, shape, budget, fan-out) then run on the candidate.
    """
    if not ops:
        return RevisionValidation(
            errors=["a revision must contain at least one operation"], candidate=None
        )

    steps = [step.model_copy(deep=True) for step in plan.steps]
    for position, op in enumerate(ops):
        error = _apply_one(steps, op)
        if error is not None:
            return RevisionValidation(errors=[f"op {position} ({op.op}): {error}"], candidate=None)

    errors = _graph_errors(steps, policy)
    errors += _budget_errors(steps, budget_remaining)
    errors += _fanout_errors(steps, max_children)
    if errors:
        return RevisionValidation(errors=errors, candidate=None)

    candidate = plan.model_copy(
        update={
            "version": plan.version + 1,
            "steps": steps,
            "revisions": list(plan.revisions),
        }
    )
    return RevisionValidation(errors=[], candidate=candidate)


# ----------------------------------------------------------------- op checks


def _apply_one(steps: list[PlanStep], op: PlanPatchOp) -> str | None:
    """Validate one op against the working copy and apply it in place.
    Returns an error string instead of applying when the op is invalid."""
    by_id = {step.id: step for step in steps}
    if op.op == "add_step":
        if op.step is None:
            return "add_step requires a step"
        if op.step.id in by_id:
            return f"step {op.step.id!r} already exists"
        if op.step.status != "pending":
            return f"added steps must start pending, got {op.step.status!r}"
        if op.after is not None and op.after not in by_id:
            return f"after references unknown step {op.after!r}"
        new_step = op.step.model_copy(deep=True)
        if op.after is None:
            steps.append(new_step)
        else:
            index = next(i for i, s in enumerate(steps) if s.id == op.after)
            steps.insert(index + 1, new_step)
        return None

    target = by_id.get(op.step_id or "")
    if target is None:
        return f"unknown step {op.step_id!r}"

    if op.op == "skip_step":
        if target.status not in _SKIPPABLE_STATUSES:
            return f"cannot skip step {target.id!r} in status {target.status!r}"
        target.status = "skipped"
        return None

    if op.op == "retry_step":
        if target.status != "failed":
            return f"cannot retry step {target.id!r} in status {target.status!r}"
        target.status = "ready"
        return None

    if op.op == "edit_step":
        if target.status not in _EDITABLE_STATUSES:
            return f"cannot edit step {target.id!r} in status {target.status!r}"
        changes = cast("dict[str, Any] | None", op.changes)  # pyright: ignore[reportUnknownMemberType]
        if not changes:
            return "edit_step requires changes"
        illegal = set(changes) - _EDITABLE_FIELDS
        if illegal:
            return f"edit_step may not change {sorted(illegal)}"
        try:
            edited = PlanStep.model_validate({**target.model_dump(), **changes})
        except ValueError as error:
            return f"invalid changes for step {target.id!r}: {error}"
        index = next(i for i, s in enumerate(steps) if s.id == target.id)
        steps[index] = edited
        return None

    if op.op == "set_budget_slice":
        if target.status not in _EDITABLE_STATUSES:
            return (
                f"cannot change the budget slice of step {target.id!r} in status {target.status!r}"
            )
        changes = cast("dict[str, Any] | None", op.changes)  # pyright: ignore[reportUnknownMemberType]
        if not changes or set(changes) != {"budget_slice"}:
            return "set_budget_slice requires changes with exactly a budget_slice"
        raw = changes["budget_slice"]
        try:
            target.budget_slice = None if raw is None else Decimal(str(raw))
        except ArithmeticError:
            return f"invalid budget_slice {raw!r}"
        return None

    return f"unknown op {op.op!r}"


# --------------------------------------------------------------- plan checks


def _graph_errors(steps: list[PlanStep], policy: RunPolicy) -> list[str]:
    errors: list[str] = []
    ids = {step.id for step in steps}
    if len(steps) > policy.max_steps:
        errors.append(f"plan has {len(steps)} steps, policy allows {policy.max_steps}")
    for step in steps:
        for dep in step.depends_on:
            if dep not in ids:
                errors.append(f"step {step.id!r} depends on unknown step {dep!r}")
        if step.id in step.depends_on:
            errors.append(f"step {step.id!r} depends on itself")
    if errors:
        return errors
    if _has_cycle(steps):
        errors.append("dependency graph has a cycle")
        return errors
    errors += _shape_errors(steps, policy.plan_shape)
    return errors


def _has_cycle(steps: list[PlanStep]) -> bool:
    remaining = {step.id: set(step.depends_on) for step in steps}
    while remaining:
        resolvable = [sid for sid, deps in remaining.items() if not deps]
        if not resolvable:
            return True
        for sid in resolvable:
            del remaining[sid]
        for deps in remaining.values():
            deps.difference_update(resolvable)
    return False


def _shape_errors(steps: list[PlanStep], shape: str) -> list[str]:
    """Structural shape check. "dag" allows anything acyclic. "linear" is a
    single self-executed chain. "linear_fanout" requires the non-group steps
    to form a chain; fan-out groups hang off it and rejoin via a join step
    (whose group-member dependencies are exempt from the chain rule)."""
    if shape == "dag" or not steps:
        return []
    errors: list[str] = []
    group_ids = {step.id for step in steps if step.group_id is not None}

    if shape == "linear":
        if group_ids:
            errors.append("linear plans cannot contain fan-out groups")
        for step in steps:
            if step.executor != "self":
                errors.append(f"linear plans require executor='self', step {step.id!r} is not")
        chain_steps = steps
        exempt: set[str] = set()
    else:  # linear_fanout
        chain_steps = [step for step in steps if step.group_id is None]
        exempt = group_ids

    dependents: dict[str, int] = {}
    roots = 0
    for step in chain_steps:
        chain_deps = [dep for dep in step.depends_on if dep not in exempt]
        if len(chain_deps) > 1:
            errors.append(f"step {step.id!r} branches: multiple chain dependencies {chain_deps}")
        if not step.depends_on:
            roots += 1
        for dep in chain_deps:
            dependents[dep] = dependents.get(dep, 0) + 1
    for sid, count in dependents.items():
        if count > 1:
            errors.append(f"step {sid!r} has {count} chain dependents; the chain may not branch")
    if roots > 1:
        errors.append(f"plan has {roots} independent chain starts; expected one")
    return errors


def _budget_errors(steps: list[PlanStep], budget_remaining: Decimal) -> list[str]:
    committed = sum(
        (
            step.budget_slice
            for step in steps
            if step.status in _BUDGET_ACTIVE_STATUSES and step.budget_slice is not None
        ),
        Decimal(0),
    )
    if committed > budget_remaining:
        return [
            f"budget slices of active steps total {committed} USD, "
            f"exceeding the {budget_remaining} USD remaining"
        ]
    return []


def _fanout_errors(steps: list[PlanStep], max_children: int) -> list[str]:
    errors: list[str] = []
    groups: dict[str, list[PlanStep]] = {}
    for step in steps:
        if step.group_id is not None:
            groups.setdefault(step.group_id, []).append(step)
    for group_id, members in groups.items():
        if len(members) > max_children:
            errors.append(
                f"group {group_id!r} has {len(members)} members, "
                f"exceeding max_children={max_children}"
            )
        shared = set(members[0].depends_on)
        for member in members:
            if member.executor != "subagent":
                errors.append(f"group {group_id!r} member {member.id!r} must be a subagent step")
            if member.subagent is None:
                errors.append(f"group {group_id!r} member {member.id!r} is missing a subagent spec")
            if set(member.depends_on) != shared:
                errors.append(
                    f"group {group_id!r} members must share dependencies; {member.id!r} differs"
                )
        member_ids = {member.id for member in members}
        join_exists = any(
            step.id not in member_ids and member_ids <= set(step.depends_on) for step in steps
        )
        if not join_exists:
            errors.append(f"group {group_id!r} has no join step depending on all members")
    return errors
