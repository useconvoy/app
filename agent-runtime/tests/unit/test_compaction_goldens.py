"""Compaction goldens: fixture transcript in, exact structured output out.

The deterministic distiller is the scripted cheap model every CI lane runs;
its outputs are pinned byte-for-byte here, at both boundaries. The activity
round-trip runs against a moto store and proves the archive discipline: the
raw transcript is never deleted, and every compaction result carries its
archive ref.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from _support.common import fixture_ref
from moto import mock_aws

from convoy_runtime.activities.compact import CompactActivities
from convoy_runtime.providers.artifact_store import ArtifactStore
from convoy_runtime.providers.compaction import (
    CompactRequest,
    distill_step_summary,
    fold_working_transcript,
)

pytestmark = pytest.mark.anyio

GOLDENS = Path(__file__).parent / "goldens"


def _load(name: str) -> dict[str, Any]:
    return json.loads((GOLDENS / name).read_text())


def test_step_end_distillation_matches_golden() -> None:
    transcript = _load("step_end_transcript.json")
    summary = distill_step_summary(
        transcript,
        run_id="run-golden-1",
        step_id="step-1",
        step_description="Investigate: the Q3 close",
        outputs=[fixture_ref("runs/run-golden-1/outputs/step-1.json")],
        archive_ref=fixture_ref("runs/run-golden-1/transcripts/step-1/turn-4.json"),
    )
    assert summary.model_dump(mode="json") == _load("step_end_summary.json")
    # The summary is bounded (~1 KB) and always points at its raw archive.
    assert len(json.dumps(summary.model_dump(mode="json"))) < 2000
    assert summary.archive_ref.key == "runs/run-golden-1/transcripts/step-1/turn-4.json"


def test_midstep_fold_matches_golden() -> None:
    transcript = _load("midstep_transcript.json")
    folded = fold_working_transcript(
        transcript,
        run_id="run-golden-2",
        step_id="step-1",
        keep_recent_turns=2,
        archive_ref=fixture_ref("runs/run-golden-2/transcripts/step-1/turn-5.json"),
    )
    assert folded == _load("midstep_folded.json")
    # The fold keeps the recent tail verbatim and links the archive.
    assert len(folded["turns"]) == 2
    assert folded["archive_ref"]["key"] == "runs/run-golden-2/transcripts/step-1/turn-5.json"
    assert folded["folded_turns"] == 8


def test_second_fold_accretes_the_rolling_note() -> None:
    first = _load("midstep_folded.json")
    grown = {
        **first,
        "turns": [
            *first["turns"],
            {"role": "user", "content": "execute step step-1 (turn 6)"},
            {"role": "assistant", "content": "echo: step step-1 turn 6"},
        ],
    }
    folded = fold_working_transcript(
        grown,
        run_id="run-golden-2",
        step_id="step-1",
        keep_recent_turns=2,
        archive_ref=fixture_ref("runs/run-golden-2/transcripts/step-1/turn-6.json"),
    )
    assert folded == _load("midstep_folded_again.json")
    # The note accretes across folds and the fold counter accumulates.
    assert folded["folded_turns"] == 10
    assert " | " in folded["progress_note"]


@pytest.fixture
def store() -> Iterator[ArtifactStore]:
    with mock_aws():
        yield ArtifactStore(
            bucket="convoy-test", region="us-east-1", access_key="test", secret_key="test"
        )


async def test_step_end_activity_archives_summary_and_keeps_raw(store: ArtifactStore) -> None:
    await store.ensure_bucket()
    transcript = _load("step_end_transcript.json")
    transcript_ref = await store.put_json(
        "runs/run-golden-1/transcripts/step-1/turn-4.json", transcript
    )
    activities = CompactActivities(store)
    result = await activities.compact_step(
        CompactRequest(
            boundary="step_end",
            run_id="run-golden-1",
            step_id="step-1",
            transcript_ref=transcript_ref,
            step_description="Investigate: the Q3 close",
            outputs=[],
        )
    )
    assert result.boundary == "step_end"
    summary = result.summary
    assert summary is not None
    assert summary.step_id == "step-1"
    assert summary.headline == "Completed step-1: Investigate: the Q3 close"
    # Archive discipline: the raw transcript is untouched and referenced.
    assert result.archived_ref == transcript_ref
    assert summary.transcript_ref == transcript_ref
    assert await store.get_json(transcript_ref) == transcript
    body: dict[str, Any] = await store.get_json(summary.summary_ref)
    assert body["archive_ref"]["key"] == transcript_ref.key


async def test_midstep_activity_writes_folded_head_and_keeps_raw(store: ArtifactStore) -> None:
    await store.ensure_bucket()
    transcript = _load("midstep_transcript.json")
    transcript_ref = await store.put_json(
        "runs/run-golden-2/transcripts/step-1/turn-5.json", transcript
    )
    activities = CompactActivities(store)
    result = await activities.compact_step(
        CompactRequest(
            boundary="mid_step",
            run_id="run-golden-2",
            step_id="step-1",
            transcript_ref=transcript_ref,
            keep_recent_turns=2,
            fold_index=1,
        )
    )
    assert result.boundary == "mid_step"
    assert result.summary is None
    assert result.archived_ref == transcript_ref
    working_ref = result.working_ref
    assert working_ref is not None
    assert working_ref.key == "runs/run-golden-2/transcripts/step-1/fold-1.json"
    folded: dict[str, Any] = await store.get_json(working_ref)
    assert folded["archive_ref"]["key"] == transcript_ref.key
    # The pre-fold raw transcript still exists, byte-identical.
    assert await store.get_json(transcript_ref) == transcript
