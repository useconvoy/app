"""Plan activities: the fixture planner stub and revision snapshot archival.

Both return *proposals or refs*; only the workflow applies plan state. The
fixture planner builds a 2-step linear plan from the pinned goal; it can
attach human gates to named steps, and it can hang a fan-out group between
the two steps (subagent members plus step-2 as the join), when the run's
pinned payload asks for them — which is how end-to-end suites exercise gate
and fan-out flows without a real model planner.

TODO: model-driven planning through the gateway.
"""

from decimal import Decimal
from typing import Any, cast

from pydantic import BaseModel, Field
from temporalio import activity

from convoy_core import (
    AgentSpec,
    ArtifactRef,
    HumanGate,
    Plan,
    PlanPatchOp,
    PlanRevision,
    PlanStep,
    RunState,
)
from convoy_runtime.activities import names
from convoy_runtime.providers.artifact_store import ArtifactStore


class FanoutFixture(BaseModel):
    """Fixture-plan fan-out request: a group of `size` subagent members
    between the investigate step and the summarize step (which becomes the
    join). Members carry `budget_slice` as their reservation and are marked
    optional so a tolerated member failure still lands as a valid partial
    completion; the join stays required. `child_layer` exists so tests can
    craft depth-guard violations."""

    size: int = Field(ge=1, le=10)
    budget_slice: Decimal | None = None
    child_layer: int = 1


def fanout_member_ids(size: int) -> list[str]:
    return [f"fan-{index}" for index in range(1, size + 1)]


def build_fixture_plan(
    goal: str,
    success_criteria: list[str],
    snapshot_ref: ArtifactRef,
    gates: dict[str, HumanGate] | None = None,
    fanout: FanoutFixture | None = None,
    *,
    agent: AgentSpec | None = None,
) -> Plan:
    """Fixture plan: two linear self-executed steps, optionally gated, with
    an optional fan-out group (member subagent specs derived from the run's
    root agent) hanging between them."""
    gates = gates or {}
    steps = [
        PlanStep(
            id="step-1",
            description=f"Investigate: {goal}",
            status="ready",
            human_gate=gates.get("step-1"),
        )
    ]
    join_depends = ["step-1"]
    if fanout is not None:
        if agent is None:
            raise ValueError("a fan-out fixture needs the root agent to derive member specs")
        member_ids = fanout_member_ids(fanout.size)
        for index, member_id in enumerate(member_ids, start=1):
            steps.append(
                PlanStep(
                    id=member_id,
                    description=f"Fan-out part {index} of: {goal}",
                    depends_on=["step-1"],
                    group_id="fanout-1",
                    executor="subagent",
                    subagent=AgentSpec(
                        id=f"{agent.id}-fan-{index}",
                        layer=fanout.child_layer,
                        parent_id=agent.id,
                        max_children=0,
                        model=agent.model,
                        tools=list(agent.tools),
                        prompt_ref=agent.prompt_ref,
                    ),
                    budget_slice=fanout.budget_slice,
                    required=False,
                    human_gate=gates.get(member_id),
                )
            )
        join_depends = member_ids
    steps.append(
        PlanStep(
            id="step-2",
            description=f"Summarize findings for: {goal}",
            depends_on=join_depends,
            human_gate=gates.get("step-2"),
        )
    )
    revision = PlanRevision(
        version=1,
        author="system",
        reason="initial",
        ops=[PlanPatchOp(op="add_step", step=step, reason="initial plan") for step in steps],
        snapshot_ref=snapshot_ref,
    )
    return Plan(
        version=1,
        goal=goal,
        success_criteria=success_criteria,
        steps=steps,
        revisions=[revision],
    )


def _parse_fixture_gates(pinned: dict[str, Any]) -> dict[str, HumanGate]:
    raw = pinned.get("fixture_gates")
    if not isinstance(raw, dict):
        return {}
    return {
        str(step_id): HumanGate.model_validate(gate)
        for step_id, gate in cast("dict[Any, Any]", raw).items()
    }


def _parse_fixture_fanout(pinned: dict[str, Any]) -> FanoutFixture | None:
    raw = pinned.get("fixture_fanout")
    if not isinstance(raw, dict):
        return None
    return FanoutFixture.model_validate(raw)


class PlanActivities:
    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    @activity.defn(name=names.CREATE_PLAN)
    async def create_plan(self, state: RunState) -> Plan:
        pinned: dict[str, Any] = await self._store.get_json(state.pinned_ref)
        goal = str(pinned.get("goal", ""))
        raw_criteria = cast(list[Any], pinned.get("success_criteria", []))
        success_criteria = [str(c) for c in raw_criteria]
        gates = _parse_fixture_gates(pinned)
        fanout = _parse_fixture_fanout(pinned)

        # Archive the full plan snapshot first (claim-check discipline): the
        # revision references it; only the ref rides through Temporal.
        snapshot_key = f"runs/{state.run_id}/plans/v1.json"
        provisional = build_fixture_plan(
            goal,
            success_criteria,
            ArtifactRef(bucket=self._store.bucket, key=snapshot_key, size_bytes=0, sha256=""),
            gates,
            fanout,
            agent=state.agent,
        )
        snapshot_ref = await self._store.put_json(snapshot_key, provisional.model_dump(mode="json"))
        plan = build_fixture_plan(
            goal, success_criteria, snapshot_ref, gates, fanout, agent=state.agent
        )
        return plan

    @activity.defn(name=names.ARCHIVE_PLAN_SNAPSHOT)
    async def archive_plan_snapshot(self, run_id: str, plan: Plan) -> ArtifactRef:
        """Archive a full plan version to the artifact store; the revision
        record carries only the returned ref."""
        return await self._store.put_json(
            f"runs/{run_id}/plans/v{plan.version}.json", plan.model_dump(mode="json")
        )
