"""Compaction: distill step transcripts into summaries and fold long ones.

Two boundaries, one discipline — lossy for context, lossless for the record:

- Step end: the step's working transcript distills into a ~1 KB structured
  `StepSummary` (headline, findings, decisions, output refs, open questions).
  The raw transcript is never deleted; the summary always carries the ref to
  its full archive.
- Mid step: when a step's working transcript grows past the token threshold,
  older turns fold into a rolling progress note while the most recent turns
  stay verbatim. The pre-fold transcript stays archived and the folded head
  links back to it, so the full chain remains walkable.

Everything here is pure and deterministic — the scripted "cheap model" every
CI lane runs, golden-tested end to end. The activity layer owns reading and
writing artifacts.

TODO: model-driven distillation via cheap-model gateway routing for
production deployments, behind this same request/result contract.
TODO: a model-facing rendering of folded transcripts so the Pydantic AI
executor can resume conversation history across a fold.
"""

from typing import Any, Literal, cast

from pydantic import BaseModel, Field

from convoy_core import ArtifactRef, StepSummaryRef

FOLDED_FORMAT = "folded-transcript"

_MAX_HEADLINE_CHARS = 120
_MAX_ITEM_CHARS = 200
_MAX_ITEMS = 5
_MAX_NOTE_CHARS = 1000
_NOTE_SNIPPET_CHARS = 120


class StepSummary(BaseModel):
    """The archived summary body a completed step leaves behind (~1 KB)."""

    run_id: str
    step_id: str
    headline: str
    findings: list[str] = []
    decisions: list[str] = []
    output_refs: list[ArtifactRef] = []
    open_questions: list[str] = []
    archive_ref: ArtifactRef  # the raw transcript this summary distills


class CompactRequest(BaseModel):
    """One compaction call, at either boundary. `transcript_ref` is the
    current working-transcript head; it doubles as the lossless archive ref
    in the result — raw transcripts are never deleted."""

    boundary: Literal["step_end", "mid_step"]
    run_id: str
    step_id: str
    transcript_ref: ArtifactRef
    step_description: str = ""
    outputs: list[ArtifactRef] = []
    keep_recent_turns: int = Field(default=2, ge=1)
    fold_index: int = Field(default=1, ge=1)


class CompactResult(BaseModel):
    """What a compaction produced. `archived_ref` is always present: the raw
    pre-compaction transcript, untouched in the artifact store."""

    boundary: Literal["step_end", "mid_step"]
    archived_ref: ArtifactRef
    summary: StepSummaryRef | None = None  # step-end distillation
    working_ref: ArtifactRef | None = None  # mid-step folded head


def _as_str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _entries(transcript: dict[str, Any]) -> list[dict[str, Any]]:
    """Role/content entries from any transcript shape this runtime writes:
    scripted and folded transcripts carry `turns`; model-format transcripts
    reduce to their final output text."""
    raw = transcript.get("turns")
    if isinstance(raw, list):
        return [cast("dict[str, Any]", e) for e in cast("list[Any]", raw) if isinstance(e, dict)]
    output = _as_str(transcript.get("output"))
    if output:
        return [{"role": "assistant", "content": output}]
    return []


def _assistant_contents(entries: list[dict[str, Any]]) -> list[str]:
    return [
        _as_str(e.get("content"))[:_MAX_ITEM_CHARS]
        for e in entries
        if e.get("role") == "assistant" and _as_str(e.get("content"))
    ]


def _string_list(transcript: dict[str, Any], key: str) -> list[str]:
    raw = transcript.get(key)
    if not isinstance(raw, list):
        return []
    return [v[:_MAX_ITEM_CHARS] for v in cast("list[Any]", raw) if isinstance(v, str)][:_MAX_ITEMS]


def distill_step_summary(
    transcript: dict[str, Any],
    *,
    run_id: str,
    step_id: str,
    step_description: str,
    outputs: list[ArtifactRef],
    archive_ref: ArtifactRef,
) -> StepSummary:
    """Deterministic step-end distillation — the scripted cheap model.

    Findings are the step's assistant outputs (bounded); decisions record
    steer assessments and promoted tool executions; a rolling progress note
    from earlier folds is preserved as a finding so nothing distilled earlier
    is lost from the summary.
    """
    entries = _entries(transcript)
    findings: list[str] = []
    note = _as_str(transcript.get("progress_note"))
    if note:
        findings.append(note[:_MAX_ITEM_CHARS])
    findings.extend(_assistant_contents(entries))

    decisions: list[str] = []
    assessment = transcript.get("assessment")
    if isinstance(assessment, dict):
        assessment_dict = cast("dict[str, Any]", assessment)
        steer_ids = assessment_dict.get("steer_ids")
        ids = (
            ",".join(str(s) for s in cast("list[Any]", steer_ids))
            if isinstance(steer_ids, list)
            else ""
        )
        decisions.append(
            f"assessed steer(s) {ids}: {_as_str(assessment_dict.get('conclusion'))}"[
                :_MAX_ITEM_CHARS
            ]
        )
    promoted = transcript.get("promoted")
    if isinstance(promoted, list):
        for entry in cast("list[Any]", promoted):
            if isinstance(entry, dict):
                entry_dict = cast("dict[str, Any]", entry)
                tool_id = _as_str(entry_dict.get("tool_id"))
                key = _as_str(entry_dict.get("idempotency_key"))
                decisions.append(f"promoted tool {tool_id} executed (key {key[:12]})")

    return StepSummary(
        run_id=run_id,
        step_id=step_id,
        headline=f"Completed {step_id}: {step_description}"[:_MAX_HEADLINE_CHARS],
        findings=findings[:_MAX_ITEMS],
        decisions=decisions[:_MAX_ITEMS],
        output_refs=list(outputs),
        open_questions=_string_list(transcript, "open_questions"),
        archive_ref=archive_ref,
    )


def fold_working_transcript(
    transcript: dict[str, Any],
    *,
    run_id: str,
    step_id: str,
    keep_recent_turns: int,
    archive_ref: ArtifactRef,
) -> dict[str, Any]:
    """Deterministic mid-step fold: older turn entries collapse into the
    rolling progress note; the most recent `keep_recent_turns` entries stay
    verbatim. The folded head records the archive ref of the pre-fold
    transcript, keeping the full-fidelity chain walkable. Step-scoped
    counters (`step_turns`) ride along so multi-turn scripting survives."""
    entries = _entries(transcript)
    older = entries[:-keep_recent_turns] if len(entries) > keep_recent_turns else []
    recent = entries[-keep_recent_turns:] if entries else []

    prior_note = _as_str(transcript.get("progress_note"))
    prior_folded = transcript.get("folded_turns")
    folded_so_far = prior_folded if isinstance(prior_folded, int) else 0

    assistant_bits = _assistant_contents(older)
    segment = f"Folded {len(older)} turn entries."
    if assistant_bits:
        segment = (
            f"Folded {len(older)} turn entries; "
            f"latest progress: {assistant_bits[-1][:_NOTE_SNIPPET_CHARS]}"
        )
    note = f"{prior_note} | {segment}" if prior_note else segment
    if len(note) > _MAX_NOTE_CHARS:
        # Recent progress matters most: keep the tail of the rolling note.
        note = "…" + note[-(_MAX_NOTE_CHARS - 1) :]

    return {
        "format": FOLDED_FORMAT,
        "run_id": run_id,
        "step_id": step_id,
        "step_turns": transcript.get("step_turns"),
        "progress_note": note,
        "folded_turns": folded_so_far + len(older),
        "archive_ref": archive_ref.model_dump(mode="json"),
        "turns": recent,
    }
