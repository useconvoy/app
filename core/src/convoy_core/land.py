"""Land report — the auditable output of `land_run`: deliverables vs success
criteria, cost breakdown, and audit pointers."""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from convoy_core.artifacts import ArtifactRef, TokenCounts


class LandReport(BaseModel):
    """Deliverables vs success criteria, cost breakdown, audit pointers."""

    run_id: str
    status: Literal["completed", "landed_partial", "failed"]
    goal: str
    success_criteria: list[str]
    deliverables: list[ArtifactRef] = []
    steps_done: int = 0
    steps_skipped: int = 0
    steps_failed: int = 0
    cost_usd: Decimal = Decimal(0)
    tokens: TokenCounts = TokenCounts()
    report_ref: ArtifactRef | None = None
