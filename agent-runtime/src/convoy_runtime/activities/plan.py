"""create_plan activity — M0 stub returning a fixture 2-step linear plan.

The activity returns a *proposal*; only the workflow applies it to `RunState`
(CLAUDE.md rule 3). TODO(milestone-1): model-driven planning via the gateway.
TODO(milestone-2): `validate_revision` + approval flow for revisions.
"""

from typing import Any, cast

from temporalio import activity

from convoy_core import ArtifactRef, Plan, PlanPatchOp, PlanRevision, PlanStep, RunState
from convoy_runtime.activities import names
from convoy_runtime.providers.artifact_store import ArtifactStore


def build_fixture_plan(goal: str, success_criteria: list[str], snapshot_ref: ArtifactRef) -> Plan:
    """The M0 fixture: two linear self-executed steps."""
    steps = [
        PlanStep(id="step-1", description=f"Investigate: {goal}", status="ready"),
        PlanStep(
            id="step-2",
            description=f"Summarize findings for: {goal}",
            depends_on=["step-1"],
        ),
    ]
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


class PlanActivities:
    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    @activity.defn(name=names.CREATE_PLAN)
    async def create_plan(self, state: RunState) -> Plan:
        pinned: dict[str, Any] = await self._store.get_json(state.pinned_ref)
        goal = str(pinned.get("goal", ""))
        raw_criteria = cast(list[Any], pinned.get("success_criteria", []))
        success_criteria = [str(c) for c in raw_criteria]

        # Archive the full plan snapshot first (claim-check discipline): the
        # revision references it; only the ref rides through Temporal.
        snapshot_key = f"runs/{state.run_id}/plans/v1.json"
        provisional = build_fixture_plan(
            goal,
            success_criteria,
            ArtifactRef(bucket=self._store.bucket, key=snapshot_key, size_bytes=0, sha256=""),
        )
        snapshot_ref = await self._store.put_json(snapshot_key, provisional.model_dump(mode="json"))
        plan = build_fixture_plan(goal, success_criteria, snapshot_ref)
        return plan
