"""Compatibility shim — the runtime seam now lives in the co-owned
convoy_core package (core/convoy_core/ports.py). Import from convoy_core in
new code; this module re-exports so existing imports keep working unchanged."""

from convoy_core.ports import *  # noqa: F401,F403
from convoy_core.ports import (  # noqa: F401
    BudgetEnvelope,
    ClockPort,
    DrainReport,
    GateResolutionWire,
    OpenGate,
    ResolveGateInput,
    RuntimeClient,
    StartMissionInput,
    SystemClock,
    system_clock,
)
