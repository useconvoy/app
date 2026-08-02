"""Executor registry -- the names scenarios/CI use to pick which agent runs in
the sandbox. Golden must pass every grader; each violator must fail exactly
the grader class it was built to trip.

Port of src/executors/index.ts.
"""

from __future__ import annotations

from typing import Dict

from ..sandbox.api import ScriptedExecutorFn
from .baseline import create_baseline_runtime_factory
from .golden import golden_renewal_prep, run_renewal_prep
from .scripted_runtime import create_scripted_runtime_factory
from .violators import (
    violator_doom_loop,
    violator_drift,
    violator_injection,
    violator_no_gate,
    violator_skips_items,
    violator_wrong_field,
)

EXECUTORS: Dict[str, ScriptedExecutorFn] = {
    "golden": golden_renewal_prep,
    "violator-no-gate": violator_no_gate,
    "violator-wrong-field": violator_wrong_field,
    "violator-skips-items": violator_skips_items,
    "violator-drift": violator_drift,
    "violator-doom-loop": violator_doom_loop,
    "violator-injection": violator_injection,
}

__all__ = [
    "EXECUTORS",
    "create_scripted_runtime_factory",
    "create_baseline_runtime_factory",
    "golden_renewal_prep",
    "run_renewal_prep",
    "violator_no_gate",
    "violator_wrong_field",
    "violator_skips_items",
    "violator_drift",
    "violator_doom_loop",
    "violator_injection",
]
