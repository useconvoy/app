"""One schedule firing: create the run through the control plane and finish.

Deliberately tiny — the run itself is a normal AgentRunWorkflow started by
the control plane's ``POST /runs`` (binding resolution, grant validation,
pinning, model keys, projections — the identical path a manual launch
walks), so a scheduled run is visible in ``GET /runs`` like any other, with
``started_via="schedule"`` attribution. The activity derives a deterministic
run id from this workflow's per-firing id, so retries never double-start."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from convoy_runtime.activities import names
from convoy_runtime.schedule_template import SCHEDULED_RUN_WORKFLOW, ScheduledRunTemplate


@workflow.defn(name=SCHEDULED_RUN_WORKFLOW)
class ScheduledRunWorkflow:
    @workflow.run
    async def run(self, template: ScheduledRunTemplate) -> str:
        return await workflow.execute_activity(
            names.CREATE_SCHEDULED_RUN,
            template,
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=2),
                maximum_interval=timedelta(seconds=30),
                maximum_attempts=5,
            ),
        )
