"""Qualify keyless paired job execution against real PostgreSQL transactions."""

from test_paired_lifecycle import (
    paired,
    test_keyless_evaluation_job_requires_api_admission_and_cannot_issue_grants,
)

__all__ = [
    "paired",
    "test_keyless_evaluation_job_requires_api_admission_and_cannot_issue_grants",
]
