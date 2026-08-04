"""Turn input/result types (transcribed verbatim from DESIGN.md section 5)."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from convoy_core.artifacts import ArtifactRef, StepSummaryRef, TokenCounts
from convoy_core.plan import HumanGate, PlanPatchOp
from convoy_core.run import SteerMessage
from convoy_core.tools import ToolCallRequest


class TurnInput(BaseModel):
    run_id: str
    step_id: str
    pinned_ref: ArtifactRef
    step_summaries: list[StepSummaryRef]
    working_transcript_ref: ArtifactRef | None
    steers: list[SteerMessage]
    budget_remaining: Decimal
    now: datetime  # via RunClock — virtual under sandbox clocks


class TurnResult(BaseModel):
    transcript_ref: ArtifactRef  # new head after this turn
    tokens: TokenCounts  # drives compaction + budget checks
    cost_usd: Decimal
    model_used: str  # actual model after any fallback
    outcome: Literal[
        "continue", "step_done", "needs_human", "propose_revision", "spawn_group", "promote"
    ]
    promoted_call: ToolCallRequest | None = None
    step_outputs: list[ArtifactRef] = []
    proposed_revision: list[PlanPatchOp] | None = None
    gate_request: HumanGate | None = None
