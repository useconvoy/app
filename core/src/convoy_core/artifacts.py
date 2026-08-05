"""Claim-check and summary reference types.

`ArtifactRef` is always an object-store pointer — blobs never ride through
Temporal payloads or histories.
"""

from pydantic import BaseModel, Field


class ArtifactRef(BaseModel):
    """Pointer to a blob in the object store. The blob itself never crosses Temporal."""

    bucket: str
    key: str
    size_bytes: int = Field(ge=0)
    sha256: str
    content_type: str = "application/octet-stream"


class TokenCounts(BaseModel):
    """Token usage for a turn; drives compaction triggers and budget checks."""

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


class Finding(BaseModel):
    """A typed key finding surfaced by a step or subagent."""

    key: str
    value: str
    confidence: float | None = None
    refs: list[ArtifactRef] = []


class StepSummaryRef(BaseModel):
    """Structured summary of a completed step (~1 KB) plus its lossless archive ref.

    Lossy for context, lossless for the record: the summary is what later turns
    see; the raw transcript archive it points at is never deleted.
    """

    step_id: str
    headline: str
    summary_ref: ArtifactRef
    transcript_ref: ArtifactRef
