"""Subagent result — the compaction contract (transcribed verbatim from DESIGN.md section 10).

The parent never ingests child transcripts; this schema is all it sees.
"""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from convoy_core.artifacts import ArtifactRef, Finding, TokenCounts


class SubagentResult(BaseModel):
    subagent_id: str
    step_id: str
    status: Literal["done", "failed", "landed_partial"]
    headline: str  # one sentence
    summary: str  # ≤ ~1 KB structured prose
    findings: list[Finding]  # typed key findings
    outputs: list[ArtifactRef]
    open_questions: list[str]
    cost_usd: Decimal
    tokens: TokenCounts
    transcript_ref: ArtifactRef  # full fidelity for evals/learning
    error_ref: ArtifactRef | None = None
