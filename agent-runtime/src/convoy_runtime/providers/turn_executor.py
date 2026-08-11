"""TurnExecutor — the owned seam around one LLM turn.

The outer loop (plan/budget/pause/steer) stays in the workflow; the inner turn
runs behind this interface. `ScriptedTurnExecutor` is the default in every
deterministic lane; the Pydantic AI executor is selected by config and talks
to real (or mock) models through the LiteLLM proxy.
"""

from collections.abc import Callable
from decimal import Decimal
from typing import Any, Protocol, cast

import anyio
from pydantic import BaseModel

from convoy_core import (
    AgentSpec,
    ArtifactRef,
    PlanPatchOp,
    PlanStep,
    SteerMessage,
    TokenCounts,
    ToolCallRequest,
    ToolGrant,
    TurnInput,
    TurnResult,
)
from convoy_runtime.providers.artifact_store import ArtifactStore
from convoy_runtime.providers.promoted import (
    PROMOTED_TOOL_ACTIVITY,
    SANDBOX_JOB_ACTIVITY,
    SANDBOX_TOOL_PREFIX,
    PromotedResume,
    promoted_call_key,
)

SCRIPTED_MODEL = "scripted-echo-1"

# A redirect steer whose body contains this marker scripts the "plan already
# covers it" assessment; any other redirect scripts a proposed revision.
PLAN_COVERS_MARKER = "[covered]"

# A step description carrying this marker scripts a multi-turn step: the
# scripted executor answers "continue" until the step has taken that many
# turns. Multi-day rehearsals and turn-limit hops are driven this way.
TURNS_MARKER_PREFIX = "[turns:"

# Progress callback for long turns; called per inline tool execution with
# {"turn": n, "tool_index": i} so the activity can heartbeat.
HeartbeatFn = Callable[[dict[str, int]], None]


class TurnContext(BaseModel):
    """Run-scoped context a turn needs beyond `TurnInput`: which agent is
    executing, the pinned environment binding, the 1-based turn number, and —
    when re-entering after a promoted call — the claim-checked result the
    turn resumes with."""

    agent: AgentSpec
    binding_ref: ArtifactRef
    turn: int
    resume: PromotedResume | None = None


class TurnExecutor(Protocol):
    """Executes exactly one turn: model call + inline tools, claim-checked."""

    async def execute_turn(
        self,
        turn: TurnInput,
        ctx: TurnContext,
        heartbeat: HeartbeatFn | None = None,
    ) -> TurnResult: ...


class ScriptedTurnExecutor:
    """Deterministic echo executor.

    Writes the turn transcript to the artifact store (full fidelity, never a
    blob through Temporal) and reports fixed token/cost numbers so budget and
    projection plumbing can be exercised without a model.

    Scripted behaviors, all driven by run data so they replay identically:

    - A step completes in one turn unless its description carries a
      `[turns:N]` marker, in which case the executor answers "continue" until
      the step has taken N turns (the per-step count rides in the working
      transcript, so it survives folds and continue_as_new hops).
    - Redirect steers script the plan-assessment contract: a redirect turn
      performs the assessment instead of step work, returning a proposed
      revision that appends a step addressing the redirect — or, when the
      redirect body carries the covered marker, no proposal, which the
      workflow records as "the plan already covers it".
    - Promoted grants script the promoted-call contract: on a step's first
      turn the executor requests each promoted tool the agent holds, one call
      per re-entry, keyed by hash(run_id, step_id, turn, call_index); tools
      named `sandbox_*` run as sandbox jobs, everything else through the
      environment's side-effect endpoint. The resumed turn records the
      claim-checked result in the transcript and then proceeds normally.
    """

    def __init__(
        self,
        store: ArtifactStore,
        model: str = SCRIPTED_MODEL,
        turn_delay_seconds: float = 0.0,
    ) -> None:
        self._store = store
        self._model = model
        # Test knob: lets e2e scenarios deterministically catch a run mid-turn.
        self._turn_delay_seconds = turn_delay_seconds

    async def execute_turn(
        self,
        turn: TurnInput,
        ctx: TurnContext,
        heartbeat: HeartbeatFn | None = None,
    ) -> TurnResult:
        if self._turn_delay_seconds > 0:
            await anyio.sleep(self._turn_delay_seconds)
        prior = await self._prior_transcript(turn)
        # A promoted-call re-entry continues the same logical turn, so the
        # per-step turn count only advances on a fresh turn.
        step_turns = self._prior_step_turns(prior) + (0 if ctx.resume is not None else 1)
        step_turns = max(step_turns, 1)
        promoted_done = self._prior_promoted(prior)

        redirects = [steer for steer in turn.steers if steer.mode == "redirect"]
        assessment = await self._assess_redirects(turn, redirects) if redirects else None

        promote: ToolCallRequest | None = None
        if assessment is None:
            promote = await self._next_promoted_call(turn, ctx, step_turns)

        transcript: dict[str, Any] = {
            "run_id": turn.run_id,
            "step_id": turn.step_id,
            "turn": ctx.turn,
            "step_turns": step_turns,
            "now": turn.now.isoformat(),
            "pinned_ref_key": turn.pinned_ref.key,
            "steers_drained": [steer.id for steer in turn.steers],
            "turns": [
                *self._prior_entries(prior),
                {"role": "user", "content": f"execute step {turn.step_id} (turn {step_turns})"},
                {"role": "assistant", "content": f"echo: step {turn.step_id} turn {step_turns}"},
            ],
        }
        if prior is not None and "progress_note" in prior:
            # A folded head carries the rolling note forward so the chain of
            # archives stays walkable from the newest transcript.
            transcript["progress_note"] = prior.get("progress_note")
            transcript["folded_turns"] = prior.get("folded_turns")
            transcript["archive_ref"] = prior.get("archive_ref")
        if ctx.resume is not None:
            promoted_done = [
                *promoted_done,
                {
                    "tool_id": ctx.resume.tool_id,
                    "idempotency_key": ctx.resume.idempotency_key,
                    "result_ref_key": ctx.resume.result_ref.key,
                },
            ]
        if promoted_done:
            transcript["promoted"] = promoted_done
        if assessment is not None:
            transcript["assessment"] = assessment["record"]
        if promote is not None:
            transcript["promote_requested"] = {
                "tool_id": promote.tool_id,
                "idempotency_key": promote.idempotency_key,
                "activity": promote.activity,
            }
        transcript_ref = await self._store.put_json(
            f"runs/{turn.run_id}/transcripts/{turn.step_id}/turn-{ctx.turn}.json", transcript
        )

        if assessment is not None:
            # An assessment turn does no step work; the step continues on the
            # next turn (with the plan possibly revised in between).
            return TurnResult(
                transcript_ref=transcript_ref,
                tokens=TokenCounts(input_tokens=12, output_tokens=7),
                cost_usd=Decimal("0.0001"),
                model_used=self._model,
                outcome=("propose_revision" if assessment["ops"] else "continue"),
                proposed_revision=assessment["ops"] or None,
            )
        if promote is not None:
            return TurnResult(
                transcript_ref=transcript_ref,
                tokens=TokenCounts(input_tokens=12, output_tokens=7),
                cost_usd=Decimal("0.0001"),
                model_used=self._model,
                outcome="promote",
                promoted_call=promote,
            )
        if step_turns < await self._wanted_turns(turn):
            return TurnResult(
                transcript_ref=transcript_ref,
                tokens=TokenCounts(input_tokens=12, output_tokens=7),
                cost_usd=Decimal("0.0001"),
                model_used=self._model,
                outcome="continue",
            )
        output_ref = await self._store.put_json(
            f"runs/{turn.run_id}/outputs/{turn.step_id}.json",
            {"step_id": turn.step_id, "result": f"echo output for {turn.step_id}"},
        )
        return TurnResult(
            transcript_ref=transcript_ref,
            tokens=TokenCounts(input_tokens=12, output_tokens=7),
            cost_usd=Decimal("0.0001"),
            model_used=self._model,
            outcome="step_done",
            step_outputs=[output_ref],
        )

    # --------------------------------------------------- transcript history

    async def _prior_transcript(self, turn: TurnInput) -> dict[str, Any] | None:
        if turn.working_transcript_ref is None:
            return None
        prior: dict[str, Any] = await self._store.get_json(turn.working_transcript_ref)
        if prior.get("step_id") != turn.step_id:
            return None
        return prior

    @staticmethod
    def _prior_step_turns(prior: dict[str, Any] | None) -> int:
        if prior is None:
            return 0
        raw = prior.get("step_turns")
        return raw if isinstance(raw, int) and raw >= 0 else 0

    @staticmethod
    def _prior_entries(prior: dict[str, Any] | None) -> list[dict[str, Any]]:
        if prior is None:
            return []
        raw = prior.get("turns")
        if not isinstance(raw, list):
            return []
        return [cast("dict[str, Any]", e) for e in cast("list[Any]", raw) if isinstance(e, dict)]

    @staticmethod
    def _prior_promoted(prior: dict[str, Any] | None) -> list[dict[str, Any]]:
        if prior is None:
            return []
        raw = prior.get("promoted")
        if not isinstance(raw, list):
            return []
        return [cast("dict[str, Any]", e) for e in cast("list[Any]", raw) if isinstance(e, dict)]

    # ------------------------------------------------------- promoted calls

    async def _next_promoted_call(
        self, turn: TurnInput, ctx: TurnContext, step_turns: int
    ) -> ToolCallRequest | None:
        """The next promoted tool this step still owes, if any. Promotion is
        static in the grant, so the sequence is replay-deterministic: the
        step's first turn walks the agent's promoted grants one call per
        re-entry."""
        if step_turns != 1:
            return None
        promoted_grants = [g for g in ctx.agent.tools if g.execution == "promoted"]
        if not promoted_grants:
            return None
        call_index = ctx.resume.call_index + 1 if ctx.resume is not None else 0
        if call_index >= len(promoted_grants):
            return None
        grant = promoted_grants[call_index]
        key = promoted_call_key(turn.run_id, turn.step_id, ctx.turn, call_index)
        args_ref = await self._store.put_json(
            f"runs/{turn.run_id}/promoted/args-{key}.json",
            self._promoted_args(grant, turn, ctx),
        )
        is_sandbox = grant.tool_id.startswith(SANDBOX_TOOL_PREFIX)
        return ToolCallRequest(
            tool_id=grant.tool_id,
            activity=SANDBOX_JOB_ACTIVITY if is_sandbox else PROMOTED_TOOL_ACTIVITY,
            args_ref=args_ref,
            idempotency_key=key,
        )

    @staticmethod
    def _promoted_args(grant: ToolGrant, turn: TurnInput, ctx: TurnContext) -> dict[str, Any]:
        """Deterministic promoted-call arguments. Sandbox jobs append a line
        to a workspace log and report its length, so workspace continuity
        across snapshots (and rebuilds after loss) is externally checkable."""
        if grant.tool_id == "sandbox_browser":
            # The deterministic lane proves the browser session is usable
            # without navigating outside the environment's allowlist.
            return {"action": "snapshot"}
        if grant.tool_id.startswith(SANDBOX_TOOL_PREFIX):
            script = (
                "mkdir -p outputs && "
                f'echo "{turn.step_id} turn {ctx.turn}" >> data.log && '
                "wc -l < data.log | tr -d ' ' > outputs/lines.txt"
            )
            return {"command": ["sh", "-c", script], "env": {}, "inputs": []}
        return {"step_id": turn.step_id, "turn": ctx.turn}

    # ----------------------------------------------------------- turn count

    async def _wanted_turns(self, turn: TurnInput) -> int:
        """How many turns this step scripts, from its description in the
        pinned header's plan render. Defaults to one."""
        description = ""
        header: dict[str, Any] = await self._store.get_json(turn.pinned_ref)
        raw_steps = header.get("plan")
        if isinstance(raw_steps, list):
            for raw in cast("list[Any]", raw_steps):
                if isinstance(raw, dict):
                    entry = cast("dict[str, Any]", raw)
                    if entry.get("id") == turn.step_id and isinstance(
                        entry.get("description"), str
                    ):
                        description = cast("str", entry["description"])
        marker = description.find(TURNS_MARKER_PREFIX)
        if marker < 0:
            return 1
        end = description.find("]", marker)
        if end < 0:
            return 1
        digits = description[marker + len(TURNS_MARKER_PREFIX) : end]
        return max(1, int(digits)) if digits.isdigit() else 1

    # ------------------------------------------------------------ redirects

    async def _last_plan_step_id(self, turn: TurnInput) -> str:
        """The final step of the plan as rendered into the pinned header —
        the anchor a proposed step is appended after, keeping the chain
        shape intact."""
        header: dict[str, Any] = await self._store.get_json(turn.pinned_ref)
        raw_steps = header.get("plan")
        last = turn.step_id
        if isinstance(raw_steps, list):
            for raw in cast("list[Any]", raw_steps):
                if not isinstance(raw, dict):
                    continue
                entry = cast("dict[str, Any]", raw)
                if isinstance(entry.get("id"), str):
                    last = cast("str", entry["id"])
        return last

    async def _assess_redirects(
        self, turn: TurnInput, redirects: list[SteerMessage]
    ) -> dict[str, Any]:
        """Deterministic redirect assessment: covered-marker redirects change
        nothing; anything else proposes appending a step that addresses the
        redirect to the end of the plan."""
        covered = [s for s in redirects if PLAN_COVERS_MARKER in s.body]
        if covered:
            return {
                "record": {
                    "steer_ids": [s.id for s in redirects],
                    "conclusion": "plan_already_covers",
                    "reason": "the current plan already addresses this redirect",
                },
                "ops": [],
            }
        primary = redirects[0]
        anchor = await self._last_plan_step_id(turn)
        new_step = PlanStep(
            id=f"step-for-{primary.id}",
            description=f"Address redirect steer: {primary.body}"[:200],
            depends_on=[anchor],
        )
        op = PlanPatchOp(
            op="add_step",
            step=new_step,
            after=anchor,
            reason=f"redirect steer {primary.id} needs work the plan does not cover",
        )
        return {
            "record": {
                "steer_ids": [s.id for s in redirects],
                "conclusion": "proposed_revision",
                "added_step": new_step.id,
            },
            "ops": [op],
        }
