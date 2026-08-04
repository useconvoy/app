"""land_run activity — graceful wrap-up producing the auditable LandReport.

Partial results are valid completion (DESIGN.md section 6.2). The full report
is archived to the artifact store; the ref rides back through Temporal.
"""

from typing import Literal

from temporalio import activity

from convoy_core import LandReport, RunState, TokenCounts
from convoy_runtime.activities import names
from convoy_runtime.providers.artifact_store import ArtifactStore


class LandActivities:
    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    @activity.defn(name=names.LAND_RUN)
    async def land_run(self, state: RunState) -> LandReport:
        plan = state.plan
        steps = plan.steps if plan else []
        done = [s for s in steps if s.status == "done"]
        skipped = [s for s in steps if s.status == "skipped"]
        failed = [s for s in steps if s.status == "failed"]
        required_incomplete = [s for s in steps if s.required and s.status != "done"]

        status: Literal["completed", "landed_partial", "failed"]
        if failed and any(s.required for s in failed):
            status = "failed"
        elif required_incomplete:
            status = "landed_partial"
        else:
            status = "completed"

        report = LandReport(
            run_id=state.run_id,
            status=status,
            goal=plan.goal if plan else "",
            success_criteria=plan.success_criteria if plan else [],
            deliverables=[ref for s in done for ref in s.outputs],
            steps_done=len(done),
            steps_skipped=len(skipped),
            steps_failed=len(failed),
            cost_usd=state.budget.spent_usd,
            tokens=TokenCounts(),  # TODO(milestone-1): aggregate real per-turn token counts
        )
        report_ref = await self._store.put_json(
            f"runs/{state.run_id}/reports/land.json", report.model_dump(mode="json")
        )
        return report.model_copy(update={"report_ref": report_ref})
