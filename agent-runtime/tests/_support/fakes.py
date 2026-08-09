"""Fake activity implementations for workflow-layer tests.

Same names and signatures as the real activities; providers are faked so the
time-skipping lane needs no network or containers. Turn behavior is scripted
per logical turn (cost, outcome, model, gate requests, proposed revisions,
promoted calls), which is how budget, approval, gate, steer, promotion, and
compaction workflow behavior is driven deterministically. Scripts are
indexed by the turn number the workflow passes, so activity retries and
promoted-call re-entries replay the same script instead of advancing it.
The fixture plan can carry human gates on named steps and a fan-out group of
subagent members. Child turns are scripted per member step (keyed by step
id, indexed per child) so concurrent children stay deterministic regardless
of interleaving, and a hold switch can pin child turns in flight to observe
concurrency windows, pause, and land cascades. Side-effecting fakes share
one in-memory journal with the container semantics: a repeated idempotency
key replays the recorded result instead of firing again.
"""

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from stub_env import SideEffectJournal
from temporalio import activity
from temporalio.exceptions import ApplicationError

from convoy_core import (
    ArtifactRef,
    HumanGate,
    LandReport,
    Plan,
    PlanPatchOp,
    RunEvent,
    RunState,
    SandboxHandle,
    SandboxJobResult,
    StepSummaryRef,
    SubagentResult,
    TokenCounts,
    ToolCallRequest,
    TurnInput,
    TurnResult,
)
from convoy_runtime.activities import names
from convoy_runtime.activities.plan import FanoutFixture, build_fixture_plan
from convoy_runtime.activities.subagent import build_subagent_result
from convoy_runtime.providers.compaction import CompactRequest, CompactResult
from convoy_runtime.providers.promoted import (
    PROMOTED_TOOL_ACTIVITY,
    SANDBOX_JOB_ACTIVITY,
    SANDBOX_TOOL_PREFIX,
    PromotedToolOutcome,
    PromotedToolRequest,
    SandboxHibernateOutcome,
    SandboxHibernateRequest,
    SandboxJobOutcome,
    SandboxJobRequest,
    SandboxRestoreOutcome,
    SandboxRestoreRequest,
    promoted_call_key,
)
from convoy_runtime.providers.turn_executor import SCRIPTED_MODEL, TurnContext
from convoy_runtime.workflows.subagent import SubagentBrief, SubagentWrapUp

from .common import fixture_ref as support_ref

FakeActivity = Callable[..., Coroutine[Any, Any, Any]]


@dataclass(frozen=True)
class ScriptedTurn:
    """One scripted run_turn response; outcome "fail" raises a non-retryable
    activity error instead of returning a result. A "needs_human" turn may
    carry the gate it requests; a "propose_revision" turn carries its ops.
    `promote_tool` scripts a promoted call: the turn's first execution
    returns outcome "promote" for that tool and the re-entry (with the
    claim-checked result in context) plays the scripted base outcome."""

    cost_usd: Decimal = Decimal("0.0001")
    outcome: Literal["continue", "step_done", "fail", "needs_human", "propose_revision"] = (
        "step_done"
    )
    model_used: str = SCRIPTED_MODEL
    input_tokens: int = 12
    output_tokens: int = 7
    gate: HumanGate | None = None
    ops: tuple[PlanPatchOp, ...] = ()
    promote_tool: str | None = None


class FakeRuntime:
    """In-memory activity set: records emitted events, scripts turn results,
    can gate the first turn to catch a run mid-flight, can attach human
    gates and a fan-out group to the fixture plan's steps, can hold child
    turns in flight until released, and journals promoted side effects."""

    def __init__(
        self,
        *,
        gate_first_turn: bool = False,
        turns: list[ScriptedTurn] | None = None,
        plan_gates: dict[str, HumanGate] | None = None,
        plan_fanout: FanoutFixture | None = None,
        child_turns: dict[str, list[ScriptedTurn]] | None = None,
        hold_child_turns: bool = False,
        fail_wrap_for: set[str] | None = None,
        fail_promoted_attempts: int = 0,
    ) -> None:
        self.events: list[RunEvent] = []
        self.turn_calls = 0
        self.turn_inputs: list[TurnInput] = []
        self.turn_contexts: list[TurnContext] = []
        self.turn_cancelled = False
        self.gate_first_turn = gate_first_turn
        self.turns = turns
        self.plan_gates = plan_gates or {}
        self.plan_fanout = plan_fanout
        self.provision_calls: list[tuple[str, Decimal]] = []
        self.assemble_calls = 0
        self.snapshots: list[Plan] = []
        self.sent_steer_ids: list[str] = []
        self.first_turn_started = asyncio.Event()
        self.release_first_turn = asyncio.Event()
        # The root run id is captured at plan creation; any turn under a
        # different run id is a child turn and follows the child scripts.
        self.root_run_id: str | None = None
        self.parent_turn_calls = 0
        self.child_turns = child_turns or {}
        self.child_turn_inputs: dict[str, list[TurnInput]] = {}
        self._child_turn_counts: dict[str, int] = {}
        # Child-turn hold switch: while set and unreleased, child turns park
        # so tests can observe in-flight children (concurrency, pause, land).
        self.hold_child_turns = hold_child_turns
        self.release_children = asyncio.Event()
        self.children_holding = 0
        self.max_children_holding = 0
        self.child_turns_started = 0
        self.cancelled_child_turns: list[str] = []
        # Subagent seam records.
        self.subagent_briefs: list[SubagentBrief] = []
        self.wrapups: list[SubagentWrapUp] = []
        self.archived_results: list[SubagentResult] = []
        self.fail_wrap_for = fail_wrap_for or set()
        # Compaction and promoted-call records.
        self.compact_requests: list[CompactRequest] = []
        self.journal = SideEffectJournal()
        self.promoted_requests: list[PromotedToolRequest] = []
        self.sandbox_requests: list[SandboxJobRequest] = []
        self.sandbox_hibernate_requests: list[SandboxHibernateRequest] = []
        self.sandbox_restore_requests: list[SandboxRestoreRequest] = []
        self._sandbox_generation = 0
        # Transient-failure injection: the first N promoted-tool executions
        # crash after journaling, so the retry proves single-fire semantics.
        self.fail_promoted_attempts = fail_promoted_attempts
        self._promoted_failures = 0

    @activity.defn(name=names.CREATE_PLAN)
    async def create_plan(self, state: RunState) -> Plan:
        self.root_run_id = state.run_id
        return build_fixture_plan(
            "test goal",
            ["it lands"],
            support_ref(f"runs/{state.run_id}/plans/v1.json"),
            self.plan_gates,
            self.plan_fanout,
            agent=state.agent,
        )

    @activity.defn(name=names.ARCHIVE_PLAN_SNAPSHOT)
    async def archive_plan_snapshot(self, run_id: str, plan: Plan) -> ArtifactRef:
        self.snapshots.append(plan)
        return support_ref(f"runs/{run_id}/plans/v{plan.version}.json")

    @activity.defn(name=names.PROVISION_MODEL_KEY)
    async def provision_model_key(self, run_id: str, cap_usd: Decimal) -> None:
        self.provision_calls.append((run_id, cap_usd))

    @activity.defn(name=names.ASSEMBLE_PINNED_HEADER)
    async def assemble_pinned_header(self, state: RunState) -> ArtifactRef:
        self.assemble_calls += 1
        return support_ref(f"runs/{state.run_id}/pinned/turn-{state.turn_count + 1}.json")

    @activity.defn(name=names.RUN_TURN)
    async def run_turn(self, turn: TurnInput, ctx: TurnContext) -> TurnResult:
        self.turn_calls += 1
        self.turn_inputs.append(turn)
        self.turn_contexts.append(ctx)
        if self.root_run_id is not None and turn.run_id != self.root_run_id:
            return await self._child_turn(turn, ctx)
        self.parent_turn_calls += 1
        if self.gate_first_turn and self.parent_turn_calls == 1:
            self.first_turn_started.set()
            try:
                await self.release_first_turn.wait()
            except asyncio.CancelledError:
                self.turn_cancelled = True
                raise
        script = ScriptedTurn()
        if self.turns:
            # Scripts are indexed by the logical turn the workflow passes, so
            # a promoted re-entry replays the same script instead of the next.
            index = min(ctx.turn - 1, len(self.turns) - 1)
            script = self.turns[index]
        if script.promote_tool is not None and ctx.resume is None:
            key = promoted_call_key(turn.run_id, turn.step_id, ctx.turn, 0)
            is_sandbox = script.promote_tool.startswith(SANDBOX_TOOL_PREFIX)
            return TurnResult(
                transcript_ref=support_ref(
                    f"runs/{turn.run_id}/transcripts/{turn.step_id}/turn-{ctx.turn}.json"
                ),
                tokens=TokenCounts(
                    input_tokens=script.input_tokens, output_tokens=script.output_tokens
                ),
                cost_usd=script.cost_usd,
                model_used=script.model_used,
                outcome="promote",
                promoted_call=ToolCallRequest(
                    tool_id=script.promote_tool,
                    activity=SANDBOX_JOB_ACTIVITY if is_sandbox else PROMOTED_TOOL_ACTIVITY,
                    args_ref=None,
                    idempotency_key=key,
                ),
            )
        return self._turn_result(turn, ctx, script)

    async def _child_turn(self, turn: TurnInput, ctx: TurnContext) -> TurnResult:
        self.child_turns_started += 1
        self.child_turn_inputs.setdefault(turn.step_id, []).append(turn)
        if self.hold_child_turns and not self.release_children.is_set():
            self.children_holding += 1
            self.max_children_holding = max(self.max_children_holding, self.children_holding)
            try:
                await self.release_children.wait()
            except asyncio.CancelledError:
                self.cancelled_child_turns.append(turn.step_id)
                raise
            finally:
                self.children_holding -= 1
        count = self._child_turn_counts.get(turn.step_id, 0)
        self._child_turn_counts[turn.step_id] = count + 1
        scripts = self.child_turns.get(turn.step_id)
        script = ScriptedTurn()
        if scripts:
            script = scripts[min(count, len(scripts) - 1)]
        return self._turn_result(turn, ctx, script)

    def _turn_result(self, turn: TurnInput, ctx: TurnContext, script: ScriptedTurn) -> TurnResult:
        if script.outcome == "fail":
            raise ApplicationError("scripted turn failure", non_retryable=True)
        return TurnResult(
            transcript_ref=support_ref(
                f"runs/{turn.run_id}/transcripts/{turn.step_id}/turn-{ctx.turn}.json"
            ),
            tokens=TokenCounts(
                input_tokens=script.input_tokens, output_tokens=script.output_tokens
            ),
            cost_usd=script.cost_usd,
            model_used=script.model_used,
            outcome=script.outcome,
            step_outputs=(
                [support_ref(f"runs/{turn.run_id}/outputs/{turn.step_id}.json")]
                if script.outcome == "step_done"
                else []
            ),
            proposed_revision=list(script.ops) if script.ops else None,
            gate_request=script.gate,
        )

    @activity.defn(name=names.COMPACT_STEP)
    async def compact_step(self, request: CompactRequest) -> CompactResult:
        """Deterministic twin of the real compaction activity: same refs and
        shapes, no artifact I/O."""
        self.compact_requests.append(request)
        if request.boundary == "step_end":
            return CompactResult(
                boundary="step_end",
                archived_ref=request.transcript_ref,
                summary=StepSummaryRef(
                    step_id=request.step_id,
                    headline=f"Completed {request.step_id}: {request.step_description}"[:120],
                    summary_ref=support_ref(
                        f"runs/{request.run_id}/summaries/{request.step_id}.json"
                    ),
                    transcript_ref=request.transcript_ref,
                ),
            )
        return CompactResult(
            boundary="mid_step",
            archived_ref=request.transcript_ref,
            working_ref=support_ref(
                f"runs/{request.run_id}/transcripts/{request.step_id}"
                f"/fold-{request.fold_index}.json"
            ),
        )

    @activity.defn(name=names.RUN_PROMOTED_TOOL)
    async def run_promoted_tool(self, request: PromotedToolRequest) -> PromotedToolOutcome:
        self.promoted_requests.append(request)
        call = request.call
        _, replayed = self.journal.record(call.tool_id, call.idempotency_key, {})
        if self._promoted_failures < self.fail_promoted_attempts:
            # The side effect landed but the activity dies before completing:
            # exactly the crash window the idempotency key exists for.
            self._promoted_failures += 1
            raise ApplicationError("injected promoted-tool crash", non_retryable=False)
        return PromotedToolOutcome(
            tool_id=call.tool_id,
            idempotency_key=call.idempotency_key,
            result_ref=support_ref(f"runs/{request.run_id}/promoted/{call.idempotency_key}.json"),
            replayed=replayed,
        )

    @activity.defn(name=names.RUN_SANDBOX_JOB)
    async def run_sandbox_job(self, request: SandboxJobRequest) -> SandboxJobOutcome:
        self.sandbox_requests.append(request)
        call = request.call
        _, replayed = self.journal.record(call.tool_id, call.idempotency_key, {})
        handle = request.handle
        if handle is None:
            self._sandbox_generation += 1
            handle = SandboxHandle(
                sandbox_id=f"sbx-fake-{self._sandbox_generation}",
                provider="fake",
                template=request.template,
            )
        return SandboxJobOutcome(
            tool_id=call.tool_id,
            idempotency_key=call.idempotency_key,
            result=SandboxJobResult(exit_code=0),
            result_ref=support_ref(f"runs/{request.run_id}/promoted/{call.idempotency_key}.json"),
            snapshot_ref=support_ref(
                f"sandboxes/{request.run_id}/snapshot-{call.idempotency_key}.tar"
            ),
            handle=handle,
            replayed=replayed,
        )

    @activity.defn(name=names.HIBERNATE_SANDBOX)
    async def hibernate_sandbox(self, request: SandboxHibernateRequest) -> SandboxHibernateOutcome:
        self.sandbox_hibernate_requests.append(request)
        return SandboxHibernateOutcome(
            checkpoint_id=request.checkpoint_id,
            reason=request.reason,
            released_sandbox_id=request.handle.sandbox_id,
            snapshot_ref=support_ref(f"sandboxes/{request.run_id}/{request.checkpoint_id}.tar"),
        )

    @activity.defn(name=names.RESTORE_SANDBOX)
    async def restore_sandbox(self, request: SandboxRestoreRequest) -> SandboxRestoreOutcome:
        self.sandbox_restore_requests.append(request)
        self._sandbox_generation += 1
        return SandboxRestoreOutcome(
            restore_id=request.restore_id,
            handle=SandboxHandle(
                sandbox_id=f"sbx-fake-{self._sandbox_generation}",
                provider="fake",
                template=request.template,
            ),
            snapshot_ref=request.snapshot_ref,
        )

    @activity.defn(name=names.ASSEMBLE_SUBAGENT_HEADER)
    async def assemble_subagent_header(self, brief: SubagentBrief) -> ArtifactRef:
        self.subagent_briefs.append(brief)
        return support_ref(f"runs/{brief.run_id}/pinned.json")

    @activity.defn(name=names.WRAP_SUBAGENT_RESULT)
    async def wrap_subagent_result(self, wrapup: SubagentWrapUp) -> SubagentResult:
        if wrapup.brief.step_id in self.fail_wrap_for:
            raise ApplicationError("scripted wrap failure", non_retryable=True)
        self.wrapups.append(wrapup)
        transcript_ref = wrapup.transcript_ref or support_ref(
            f"runs/{wrapup.brief.run_id}/transcripts/{wrapup.brief.step_id}/none.json"
        )
        error_ref = (
            support_ref(f"runs/{wrapup.brief.run_id}/errors/wrapup.json") if wrapup.error else None
        )
        return build_subagent_result(wrapup, transcript_ref, error_ref)

    @activity.defn(name=names.ARCHIVE_SUBAGENT_RESULT)
    async def archive_subagent_result(
        self, parent_run_id: str, result: SubagentResult
    ) -> ArtifactRef:
        self.archived_results.append(result)
        return support_ref(f"runs/{parent_run_id}/subagents/{result.step_id}/result.json")

    @activity.defn(name=names.LAND_RUN)
    async def land_run(self, state: RunState, tokens: TokenCounts) -> LandReport:
        plan = state.plan
        assert plan is not None
        done = [s for s in plan.steps if s.status == "done"]
        failed = [s for s in plan.steps if s.status == "failed"]
        required_incomplete = [s for s in plan.steps if s.required and s.status != "done"]
        status: Literal["completed", "landed_partial", "failed"]
        if failed and any(s.required for s in failed):
            status = "failed"
        elif required_incomplete:
            status = "landed_partial"
        else:
            status = "completed"
        return LandReport(
            run_id=state.run_id,
            status=status,
            goal=plan.goal,
            success_criteria=plan.success_criteria,
            deliverables=[ref for s in done for ref in s.outputs],
            steps_done=len(done),
            steps_skipped=len([s for s in plan.steps if s.status == "skipped"]),
            steps_failed=len(failed),
            cost_usd=state.budget.spent_usd,
            tokens=tokens,
        )

    @activity.defn(name=names.EMIT_RUN_EVENTS)
    async def emit_run_events(self, events: list[RunEvent]) -> None:
        known = {e.id for e in self.events}
        self.events.extend(e for e in events if e.id not in known)

    @property
    def activities(self) -> list[FakeActivity]:
        return [
            self.create_plan,
            self.archive_plan_snapshot,
            self.provision_model_key,
            self.assemble_pinned_header,
            self.run_turn,
            self.compact_step,
            self.run_promoted_tool,
            self.run_sandbox_job,
            self.hibernate_sandbox,
            self.restore_sandbox,
            self.assemble_subagent_header,
            self.wrap_subagent_result,
            self.archive_subagent_result,
            self.land_run,
            self.emit_run_events,
        ]

    @property
    def event_types(self) -> list[str]:
        return [e.type for e in self.events]

    def events_of(self, event_type: str) -> list[RunEvent]:
        return [e for e in self.events if e.type == event_type]
