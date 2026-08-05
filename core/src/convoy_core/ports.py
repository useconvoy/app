"""The co-signed runtime seam — the ONLY things agent-evals asks of agent-runtime.

Python port of src/runtime/ports.ts (branch agent-evals-harness). DOMAIN TIME
(event ts, gate deadlines, timer fire_at, the "today is..." prompt string)
flows through ClockPort; MECHANICAL TIME (timeouts, liveness) stays wall-clock.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .mission_events import GateId, GateKind, MissionId


class ClockPort(abc.ABC):
    """Seam 1. Production injects the system clock."""

    @abc.abstractmethod
    def now(self) -> datetime:
        """Current DOMAIN time (always tz-aware UTC)."""


class SystemClock(ClockPort):
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


system_clock = SystemClock()


@dataclass
class OpenGate:
    gateId: GateId
    kind: GateKind
    deadlineAt: Optional[datetime]
    payload: Any
    stepTag: Optional[str] = None


@dataclass
class DrainReport:
    """Seam 2 result: the mission ran until quiescent."""

    terminal: Optional[str]  # 'landed' | 'cancelled' | 'failed' | None
    openGates: List[OpenGate]
    nextTimerAt: Optional[datetime]
    stepsExecuted: int


@dataclass
class BudgetEnvelope:
    usd: float
    tokens: Optional[int] = None
    simDeadline: Optional[datetime] = None


@dataclass
class StartMissionInput:
    """Seam 4. Programmatic mission start — no console required."""

    missionType: str
    environmentId: str  # Seam 3: resolved by the gateway to per-tool bindings.
    goal: str
    params: Dict[str, Any] = field(default_factory=dict)
    overrides: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GateResolutionWire:
    kind: str  # GateResolutionKind
    reason: Optional[str] = None
    payload: Any = None
    addUsd: Optional[float] = None
    patch: Any = None


@dataclass
class ResolveGateInput:
    """Seam 5. Attribution is persisted verbatim in the log."""

    gateId: GateId
    resolution: GateResolutionWire
    resolvedBy: str
    reason: Optional[str] = None


class RuntimeClient(abc.ABC):
    """The full runtime surface the harness drives. The asyncio ScriptedRuntime
    (pre-skeleton) and the real runtime client both implement this — which is
    what lets the harness be built and tested before agent-runtime exists."""

    @abc.abstractmethod
    async def start_mission(self, input: StartMissionInput, clock: ClockPort) -> MissionId: ...

    @abc.abstractmethod
    async def drain(self, mission_id: MissionId, clock: ClockPort) -> DrainReport:
        """Run the mission until quiescent — every live attempt finished, all
        remaining work blocked on a future timer or an open gate."""

    @abc.abstractmethod
    async def resolve_gate(self, input: ResolveGateInput, clock: ClockPort) -> None: ...
