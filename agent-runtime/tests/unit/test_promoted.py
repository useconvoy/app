"""Promoted-call plumbing: deterministic idempotency keys, and the activity
name registry staying in lockstep with the promoted-call constants (which
cannot import it without a cycle)."""

from convoy_runtime.activities import names
from convoy_runtime.providers.promoted import (
    PROMOTED_ACTIVITIES,
    PROMOTED_TOOL_ACTIVITY,
    SANDBOX_JOB_ACTIVITY,
    promoted_call_key,
)


def test_key_is_deterministic_and_unique_per_call_coordinates() -> None:
    key = promoted_call_key("run-1", "step-1", 3, 0)
    assert key == promoted_call_key("run-1", "step-1", 3, 0)
    assert len(key) == 64 and int(key, 16) >= 0  # sha256 hex
    others = {
        promoted_call_key("run-2", "step-1", 3, 0),
        promoted_call_key("run-1", "step-2", 3, 0),
        promoted_call_key("run-1", "step-1", 4, 0),
        promoted_call_key("run-1", "step-1", 3, 1),
    }
    assert key not in others
    assert len(others) == 4


def test_activity_name_registry_mirrors_promoted_constants() -> None:
    assert names.RUN_PROMOTED_TOOL == PROMOTED_TOOL_ACTIVITY
    assert names.RUN_SANDBOX_JOB == SANDBOX_JOB_ACTIVITY
    assert {names.RUN_PROMOTED_TOOL, names.RUN_SANDBOX_JOB} == PROMOTED_ACTIVITIES
