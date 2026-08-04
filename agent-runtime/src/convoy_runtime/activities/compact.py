"""compact_step activity — both compaction boundaries.

Step end: read the step's working transcript, distill it into the structured
`StepSummary`, archive the summary, and hand back a `StepSummaryRef` whose
`transcript_ref` points at the untouched raw archive. Mid step: fold older
turns of the working transcript into the rolling progress note, write the
folded head as a new artifact, and report both the new head and the archived
pre-fold ref. Raw transcripts are never deleted at either boundary.

The distillation itself is the deterministic scripted cheap model in
`providers/compaction.py`; this layer only moves artifacts.
"""

from typing import Any

from temporalio import activity

from convoy_core import StepSummaryRef
from convoy_runtime.activities import names
from convoy_runtime.providers.artifact_store import ArtifactStore
from convoy_runtime.providers.compaction import (
    CompactRequest,
    CompactResult,
    distill_step_summary,
    fold_working_transcript,
)


class CompactActivities:
    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    @activity.defn(name=names.COMPACT_STEP)
    async def compact_step(self, request: CompactRequest) -> CompactResult:
        transcript: dict[str, Any] = await self._store.get_json(request.transcript_ref)
        if request.boundary == "step_end":
            summary = distill_step_summary(
                transcript,
                run_id=request.run_id,
                step_id=request.step_id,
                step_description=request.step_description,
                outputs=request.outputs,
                archive_ref=request.transcript_ref,
            )
            summary_ref = await self._store.put_json(
                f"runs/{request.run_id}/summaries/{request.step_id}.json",
                summary.model_dump(mode="json"),
            )
            return CompactResult(
                boundary="step_end",
                archived_ref=request.transcript_ref,
                summary=StepSummaryRef(
                    step_id=request.step_id,
                    headline=summary.headline,
                    summary_ref=summary_ref,
                    transcript_ref=request.transcript_ref,
                ),
            )
        folded = fold_working_transcript(
            transcript,
            run_id=request.run_id,
            step_id=request.step_id,
            keep_recent_turns=request.keep_recent_turns,
            archive_ref=request.transcript_ref,
        )
        working_ref = await self._store.put_json(
            f"runs/{request.run_id}/transcripts/{request.step_id}/fold-{request.fold_index}.json",
            folded,
        )
        return CompactResult(
            boundary="mid_step",
            archived_ref=request.transcript_ref,
            working_ref=working_ref,
        )
