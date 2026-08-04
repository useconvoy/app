"""Re-record checked-in replay histories.

Run via `make record-history`. Re-record only with a rationale in the commit
message; prefer `workflow.patched` versioning for live-run compatibility.

Four representative histories are recorded: the linear happy path; a
budget-exhausted run under the land policy (warning + exhaustion + landing);
a human-in-the-loop run exercising plan approval, a redirect steer whose
assessed revision is approved mid-run, and an answered human gate; and a
fan-out run whose two children spawn concurrently with one member failing
under join_with_partials — so replay coverage includes the budget, approval,
steer, gate, and spawn/gather/reservation surfaces.
"""

import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path

from temporalio.worker import Worker

TESTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TESTS_DIR))

from _support.common import (  # noqa: E402
    TEST_TASK_QUEUE,
    fixture_run_state,
    start_time_skipping_env,
)
from _support.fakes import FakeRuntime, ScriptedTurn  # noqa: E402

from convoy_core import (  # noqa: E402
    HumanGate,
    PlanPatchOp,
    PlanStep,
    RunPolicy,
    RunState,
    SteerMessage,
)
from convoy_runtime.activities.plan import FanoutFixture  # noqa: E402
from convoy_runtime.signals import GateResponse, PlanApprovalDecision  # noqa: E402
from convoy_runtime.workflows.agent_run import AgentRunWorkflow  # noqa: E402
from convoy_runtime.workflows.subagent import SubagentWorkflow  # noqa: E402

HISTORIES_DIR = Path(__file__).resolve().parent
RECORDER = "recorder@convoy.test"


async def _wait_event(fake: FakeRuntime, event_type: str, occurrence: int = 1) -> None:
    async with asyncio.timeout(30):
        while fake.event_types.count(event_type) < occurrence:  # noqa: ASYNC110
            await asyncio.sleep(0.05)


async def _record(name: str, fake: FakeRuntime, state: RunState) -> Path:
    env = await start_time_skipping_env()
    async with (
        env,
        Worker(
            env.client,
            task_queue=TEST_TASK_QUEUE,
            workflows=[AgentRunWorkflow, SubagentWorkflow],
            activities=fake.activities,
        ),
    ):
        handle = await env.client.start_workflow(
            AgentRunWorkflow.run,
            args=[state, RECORDER],
            id=state.run_id,
            task_queue=TEST_TASK_QUEUE,
        )
        await handle.result()
        history = await handle.fetch_history()

    out = HISTORIES_DIR / f"{name}.json"
    out.write_text(json.dumps(history.to_json_dict(), indent=2, sort_keys=True) + "\n")
    return out


async def _record_human_in_the_loop(name: str) -> Path:
    """Approval + steer + gate choreography: the initial plan is approved, a
    redirect steer produces an assessed revision that is approved mid-run,
    and step-2's attached gate is answered."""
    fake = FakeRuntime(
        turns=[
            ScriptedTurn(
                outcome="propose_revision",
                ops=(
                    PlanPatchOp(
                        op="add_step",
                        step=PlanStep(
                            id="step-3",
                            description="Compile the risk register",
                            depends_on=["step-2"],
                        ),
                        after="step-2",
                        reason="redirect steer asked for a risk register",
                    ),
                ),
            ),
            ScriptedTurn(),
        ],
        plan_gates={"step-2": HumanGate(kind="approval", prompt="Proceed to the summary?")},
    )
    state = fixture_run_state(
        run_id="run-history-m2-hitl",
        policy=RunPolicy(require_plan_approval=True, approval_scope="major_revisions"),
    )
    env = await start_time_skipping_env()
    async with (
        env,
        Worker(
            env.client,
            task_queue=TEST_TASK_QUEUE,
            workflows=[AgentRunWorkflow, SubagentWorkflow],
            activities=fake.activities,
        ),
    ):
        handle = await env.client.start_workflow(
            AgentRunWorkflow.run,
            args=[state, RECORDER],
            id=state.run_id,
            task_queue=TEST_TASK_QUEUE,
        )
        await _wait_event(fake, "plan_created")
        await handle.signal(
            AgentRunWorkflow.steer,
            SteerMessage(
                id="steer-hitl-1",
                author="human",
                author_id=RECORDER,
                mode="redirect",
                body="Also produce a risk register",
            ),
        )
        await _wait_event(fake, "steer_received")
        await handle.signal(
            AgentRunWorkflow.approve_plan,
            PlanApprovalDecision(plan_version=1, approve=True, reason=None, actor=RECORDER),
        )
        await _wait_event(fake, "revision_applied")
        await handle.signal(
            AgentRunWorkflow.approve_plan,
            PlanApprovalDecision(plan_version=2, approve=True, reason=None, actor=RECORDER),
        )
        await _wait_event(fake, "gate_opened")
        await handle.signal(
            AgentRunWorkflow.human_response,
            GateResponse(step_id="step-2", response="approved, proceed", actor=RECORDER),
        )
        await handle.result()
        history = await handle.fetch_history()

    out = HISTORIES_DIR / f"{name}.json"
    out.write_text(json.dumps(history.to_json_dict(), indent=2, sort_keys=True) + "\n")
    return out


async def _record_fanout(name: str) -> list[Path]:
    """Fan-out choreography: two children spawned concurrently, reservations
    carved and refunded, one member failing into join_with_partials, the join
    proceeding over the gap. The parent's history and both children's own
    histories are recorded, so the spawn/gather surface and the child
    workflow (clean and failing wrap-ups) are all replay-guarded."""
    fake = FakeRuntime(
        plan_fanout=FanoutFixture(size=2, budget_slice=Decimal("1.00")),
        child_turns={
            "fan-1": [ScriptedTurn(cost_usd=Decimal("0.25"))],
            "fan-2": [ScriptedTurn(outcome="fail")],
        },
    )
    state = fixture_run_state(
        run_id="run-history-fanout",
        max_children=2,
        policy=RunPolicy(require_plan_approval=False, max_parallel=2),
    )
    env = await start_time_skipping_env()
    histories: dict[str, str] = {}
    async with (
        env,
        Worker(
            env.client,
            task_queue=TEST_TASK_QUEUE,
            workflows=[AgentRunWorkflow, SubagentWorkflow],
            activities=fake.activities,
        ),
    ):
        handle = await env.client.start_workflow(
            AgentRunWorkflow.run,
            args=[state, RECORDER],
            id=state.run_id,
            task_queue=TEST_TASK_QUEUE,
        )
        await handle.result()
        parent_history = await handle.fetch_history()
        histories[name] = json.dumps(parent_history.to_json_dict(), indent=2, sort_keys=True)
        for member, suffix in (("fan-1", "child_done"), ("fan-2", "child_failed")):
            child_handle = env.client.get_workflow_handle(f"{state.run_id}--{member}-a1")
            child_history = await child_handle.fetch_history()
            histories[f"{name}_{suffix}"] = json.dumps(
                child_history.to_json_dict(), indent=2, sort_keys=True
            )

    paths: list[Path] = []
    for history_name, payload in histories.items():
        out = HISTORIES_DIR / f"{history_name}.json"
        out.write_text(payload + "\n")
        paths.append(out)
    return paths


async def record_all() -> list[Path]:
    return [
        await _record(
            "happy_path", FakeRuntime(), fixture_run_state(run_id="run-history-happy-path")
        ),
        await _record(
            "budget_exhausted_land",
            FakeRuntime(
                turns=[
                    ScriptedTurn(cost_usd=Decimal("0.85")),
                    ScriptedTurn(cost_usd=Decimal("0.20"), outcome="continue"),
                ]
            ),
            fixture_run_state(
                run_id="run-history-budget-land",
                budget_cap_usd=Decimal("1.00"),
                policy=RunPolicy(require_plan_approval=False, on_budget_exhausted="land"),
            ),
        ),
        await _record_human_in_the_loop("human_in_the_loop"),
        *await _record_fanout("fanout_partial_join"),
    ]


def main() -> None:
    for path in asyncio.run(record_all()):
        print(f"recorded {path}")


if __name__ == "__main__":
    main()
