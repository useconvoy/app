"""SandboxInstance — one live simulated environment per scenario run, plus the
DES driver (run_until): drain the runtime to quiescence, apply the approval
script, jump the sim clock to the next scheduled event (timer, counterparty
delivery, or gate resolution), deliver, repeat. Nothing here sleeps on wall
time — a 45-day mission runs in milliseconds.

Port of src/sandbox/instance.ts.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..runtime.log import EventLog
from ..runtime.ports import (
    DrainReport,
    GateResolutionWire,
    OpenGate,
    ResolveGateInput,
    StartMissionInput,
)
from ..schema.scenario import Scenario, parse_duration_ms
from .api import (
    RunReport,
    RuntimeFactory,
    SandboxInstance,
    SandboxService,
    StopCondition,
    ToolEmulator,
)
from .clock import create_sim_clock
from .counterparty import create_counterparty_engine
from .emulators.ams import ams_emulator
from .emulators.calendar import calendar_emulator
from .emulators.carrier_portal import carrier_portal_emulator
from .emulators.email import email_emulator
from .gates import create_gate_script_engine
from .gateway import create_tool_gateway
from .world import create_world_store


def default_emulators() -> List[ToolEmulator]:
    return [*email_emulator, *ams_emulator, *carrier_portal_emulator, *calendar_emulator]


_HERE = Path(__file__).resolve().parent
DEFAULT_PACK_ROOT = str(_HERE.parent.parent / "scenarios" / "packs")


def _parse_t0(t0: str) -> datetime:
    s = t0 if "T" in t0 else "%sT00:00:00Z" % t0
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("scenario.t0 is not a valid date: %s" % t0)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


class _SandboxInstance(SandboxInstance):
    def __init__(self, **kw: Any) -> None:
        self.id = kw["id"]
        self.environmentId = kw["id"]
        self.clock = kw["clock"]
        self.world = kw["world"]
        self.log = kw["log"]
        self.missionId = kw["missionId"]
        self._runtime = kw["runtime"]
        self._counterparties = kw["counterparties"]
        self._gates = kw["gates"]
        self._scenario = kw["scenario"]
        self._sim_deadline: datetime = kw["sim_deadline"]
        self._wall_limit_ms: float = kw["wall_limit_ms"]
        self._usd_spent = 0.0
        self._resolve_now_calls = 0

        def on_append(e: Any) -> None:
            if e.type == "budget_debit" and e.missionId == self.missionId:
                self._usd_spent += e.usd

        self.log.on_append(on_append)

    # The label factory: every scripted resolution is a labeled human intervention.
    async def _resolver(
        self,
        gate_id: str,
        resolution: GateResolutionWire,
        resolved_by: str,
        reason: Optional[str],
    ) -> None:
        self._resolve_now_calls += 1
        await self._runtime.resolve_gate(
            ResolveGateInput(
                gateId=gate_id, resolution=resolution, resolvedBy=resolved_by, reason=reason
            ),
            self.clock,
        )
        self.log.append(
            {
                "type": "human_intervention",
                "missionId": self.missionId,
                "kind": "gate_resolution",
                "reason": reason,
            }
        )

    @staticmethod
    def _diagnose(r: DrainReport) -> str:
        gates_part = (
            "%d open gate(s) unmatched by script" % len(r.openGates)
            if r.openGates
            else "no open gates"
        )
        return (
            "deadlock: no pending timers, no queued counterparty deliveries, "
            "no scheduled gate resolutions; %s — nothing will ever wake this mission" % gates_part
        )

    async def _drive(self, stop: StopCondition, max_cycles: float) -> RunReport:
        """Drive the DES loop; max_cycles caps iterations (step() passes 1)."""
        wall_start = time.monotonic()
        steps_executed = 0
        last_open_gates: List[OpenGate] = []

        def report(**over: Any) -> RunReport:
            base: Dict[str, Any] = {
                "terminal": None,
                "deadlock": False,
                "guardTripped": None,
                "openGates": last_open_gates,
                "simNow": self.clock.now(),
                "wallMs": (time.monotonic() - wall_start) * 1000,
                "stepsExecuted": steps_executed,
            }
            base.update(over)
            return RunReport(**base)

        cycle = 0
        while cycle < max_cycles:
            cycle += 1
            r = await self._runtime.drain(self.missionId, self.clock)
            steps_executed += r.stepsExecuted
            last_open_gates = r.openGates

            if r.terminal:
                return report(terminal=r.terminal)
            if stop["kind"] == "gate-open" and len(r.openGates) > 0:
                return report()
            if stop["kind"] == "sim-time" and self.clock.now() >= stop["at"]:
                return report()

            before = self._resolve_now_calls
            await self._gates.apply_scripts(r.openGates, self._resolver)
            if self._resolve_now_calls > before:
                continue  # a gate resolved NOW → drain again without advancing

            candidates = [
                d
                for d in (
                    r.nextTimerAt,
                    self._counterparties.peek(),
                    self._gates.next_resolution_at(),
                )
                if d is not None
            ]
            if not candidates:
                return report(deadlock=True, deadlockDiagnosis=self._diagnose(r))
            next_at = min(candidates)

            if stop["kind"] == "sim-time" and next_at > stop["at"]:
                self.clock.advance_to(stop["at"])
                return report()
            if next_at > self._sim_deadline:
                return report(guardTripped="sim")
            if (time.monotonic() - wall_start) * 1000 > self._wall_limit_ms:
                return report(guardTripped="wall")
            if self._usd_spent > self._scenario.budgets.usd:
                return report(guardTripped="usd")

            self.clock.advance_to(next_at)
            self._counterparties.deliver_due(self.clock.now())
            await self._gates.resolve_due(self.clock.now(), self._resolver)
        return report()

    async def run_until(self, stop: StopCondition) -> RunReport:
        return await self._drive(stop, float("inf"))

    async def step(self) -> RunReport:
        """One drain+advance cycle, for debugging."""
        return await self._drive({"kind": "terminal"}, 1)

    def gate_report(self):
        return self._gates.report()

    def destroy(self) -> Dict[str, Any]:
        return {"events": self.log.for_mission(self.missionId), "world": self.world.export_bundle()}


class _SandboxService(SandboxService):
    def __init__(self, pack_root: Optional[str] = None) -> None:
        self._pack_root = pack_root or DEFAULT_PACK_ROOT

    async def create(
        self,
        scenario: Scenario,
        runtime_factory: RuntimeFactory,
        pack_dir: Optional[str] = None,
    ) -> SandboxInstance:
        t0 = _parse_t0(scenario.t0)
        clock = create_sim_clock(t0)
        log = EventLog(clock)
        world = create_world_store(clock, scenario.seed)
        resolved_pack_dir = pack_dir or str(Path(self._pack_root) / scenario.fixture.pack)
        world.seed_from_pack(resolved_pack_dir, t0, scenario.seed)

        gateway = create_tool_gateway(
            emulators=default_emulators(),
            bindings=scenario.bindings or {},  # empty ⇒ every tool defaults to its emulator
            log=log,
            world=world,
            mission_budget_usd=scenario.budgets.usd,
        )

        # Engines attach BEFORE the trigger advance so unsolicited sends and
        # world subscriptions are anchored at the scenario epoch.
        counterparties = create_counterparty_engine(
            scripts=scenario.counterparties,
            world=world,
            clock=clock,
            seed=scenario.seed,
            pack_dir=resolved_pack_dir,
        )
        gates = create_gate_script_engine(scenario.approvals, clock, scenario.seed)

        runtime = runtime_factory(
            {"gateway": gateway, "log": log, "world": world, "clock": clock}
        )

        instance_id = "sim_%s" % os.urandom(4).hex()

        # Schedule triggers advance the clock to t0+atSim BEFORE the mission starts.
        if scenario.trigger.kind == "schedule":
            clock.advance_to(t0 + timedelta(milliseconds=parse_duration_ms(scenario.trigger.atSim)))
        spec = scenario.trigger.missionSpec
        mission_id = await runtime.start_mission(
            StartMissionInput(
                missionType=spec.missionType,
                environmentId=instance_id,
                goal=spec.goal,
                params=spec.params or {},
            ),
            clock,
        )

        sim_deadline = t0 + timedelta(milliseconds=parse_duration_ms(scenario.budgets.simTime))
        wall_limit_ms = parse_duration_ms(scenario.budgets.wallClock)

        return _SandboxInstance(
            id=instance_id,
            clock=clock,
            world=world,
            log=log,
            missionId=mission_id,
            runtime=runtime,
            counterparties=counterparties,
            gates=gates,
            scenario=scenario,
            sim_deadline=sim_deadline,
            wall_limit_ms=wall_limit_ms,
        )


def create_sandbox_service(pack_root: Optional[str] = None) -> SandboxService:
    return _SandboxService(pack_root)
