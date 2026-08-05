"""GateScriptEngine — the ApprovalScript interpreter: simultaneously the
unattended gate RESOLVER (so scenarios run without a human) and an
expected-gate ASSERTION set (unexpected gates and never-raised steps are
report material for graders).

Port of src/sandbox/gates.ts. afterSim on a step simulates human latency: the
resolution is queued on the sim timeline and fed into the DES next-event
computation, which exercises park-and-resume for free.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ..runtime.ports import ClockPort, GateResolutionWire, OpenGate
from ..schema.match import matches
from ..schema.scenario import (
    ApprovalScript,
    AutoApproveScript,
    AutoRejectScript,
    GateMatcher,
    GateScriptStep,
    ScriptedApprovals,
    parse_duration_ms,
)
from .api import GateScriptReport, GateScriptResolution
from .counterparty import mulberry32

# (gate_id, resolution, resolved_by, reason) -> awaitable
GateResolverFn = Callable[[str, GateResolutionWire, str, Optional[str]], Awaitable[None]]


def _parse_script(script: Any) -> Any:
    if isinstance(script, (AutoApproveScript, AutoRejectScript, ScriptedApprovals)):
        return script
    from pydantic import TypeAdapter

    return TypeAdapter(ApprovalScript).validate_python(script)


def _to_wire(r: Any) -> GateResolutionWire:
    kind = r.kind
    if kind == "approve":
        return GateResolutionWire(kind="approve")
    if kind == "reject":
        return GateResolutionWire(kind="reject", reason=r.reason)
    if kind == "provide_input":
        return GateResolutionWire(kind="provide_input", payload=r.payload)
    if kind == "raise_budget":
        return GateResolutionWire(kind="raise_budget", addUsd=r.addUsd)
    if kind == "edit_then_approve":
        return GateResolutionWire(kind="edit_then_approve", patch=r.patch)  # patch rides the wire
    if kind == "expire":
        return GateResolutionWire(kind="expire")
    raise ValueError("unknown gate resolution kind: %s" % kind)


def _gate_matches(matcher: GateMatcher, gate: OpenGate) -> bool:
    if matcher.kind and matcher.kind != gate.kind:
        return False
    if matcher.stepTag and matcher.stepTag != gate.stepTag:
        return False
    if matcher.payload is not None and not matches(matcher.payload, gate.payload):
        return False
    return True


class _StepState:
    __slots__ = ("step", "fires")

    def __init__(self, step: GateScriptStep) -> None:
        self.step = step
        self.fires = 0


class _ScheduledResolution:
    __slots__ = ("at", "seq", "gate_id", "wire", "step_label", "resolution_kind", "reason")

    def __init__(
        self,
        at: datetime,
        seq: int,
        gate_id: str,
        wire: GateResolutionWire,
        step_label: str,
        resolution_kind: str,
        reason: Optional[str],
    ) -> None:
        self.at = at
        self.seq = seq
        self.gate_id = gate_id
        self.wire = wire
        self.step_label = step_label
        self.resolution_kind = resolution_kind
        self.reason = reason


class GateScriptEngine:
    def __init__(self, script: Any, clock: ClockPort, seed: int) -> None:
        self._script = _parse_script(script)
        self._clock = clock
        # Decorrelate from the counterparty stream.
        self._rand = mulberry32((seed ^ 0x9E3779B9) & 0xFFFFFFFF)

        self._steps: List[_StepState] = (
            [_StepState(step) for step in self._script.steps]
            if self._script.mode == "scripted"
            else []
        )
        self._ordered_idx = [i for i, s in enumerate(self._steps) if s.step.ordered]
        self._ordered_pointer = 0

        self._handled: set = set()
        self._scheduled: List[_ScheduledResolution] = []
        self._schedule_seq = 0
        self._approved_count = 0

        self._resolutions: List[GateScriptResolution] = []
        self._unexpected: List[Dict[str, str]] = []

    def _delay_ms(self, d: Any) -> float:
        if isinstance(d, str):
            return parse_duration_ms(d)
        lo = parse_duration_ms(d.min if not isinstance(d, dict) else d["min"])
        hi = parse_duration_ms(d.max if not isinstance(d, dict) else d["max"])
        return lo + self._rand() * (hi - lo)

    async def _resolve(
        self,
        gate_id: str,
        wire: GateResolutionWire,
        step_label: str,
        resolution_kind: str,
        reason: Optional[str],
        after_sim: Any,
        resolve_now: GateResolverFn,
    ) -> bool:
        if after_sim is not None:
            at = self._clock.now() + timedelta(milliseconds=self._delay_ms(after_sim))
            self._scheduled.append(
                _ScheduledResolution(
                    at, self._schedule_seq, gate_id, wire, step_label, resolution_kind, reason
                )
            )
            self._schedule_seq += 1
            self._scheduled.sort(key=lambda s: (s.at, s.seq))
            return False
        await resolve_now(gate_id, wire, "harness:%s" % step_label, reason)
        self._resolutions.append(
            GateScriptResolution(gateId=gate_id, stepId=step_label, resolution=resolution_kind)
        )
        return True

    def _find_scripted_step(self, gate: OpenGate) -> Optional[_StepState]:
        """Ordered steps must match in listed order (optional ones may be
        skipped); unordered match anytime."""
        j = self._ordered_pointer
        while j < len(self._ordered_idx):
            state = self._steps[self._ordered_idx[j]]
            if state.fires >= state.step.maxFires:
                if j == self._ordered_pointer:
                    self._ordered_pointer += 1
                j += 1
                continue
            if _gate_matches(state.step.expect, gate):
                state.fires += 1
                self._ordered_pointer = j + 1 if state.fires >= state.step.maxFires else j
                return state
            if not state.step.optional:
                break  # a required ordered step blocks everything after it
            j += 1
        for state in self._steps:
            if state.step.ordered or state.fires >= state.step.maxFires:
                continue
            if _gate_matches(state.step.expect, gate):
                state.fires += 1
                return state
        return None

    async def apply_scripts(self, open_gates: List[OpenGate], resolve_now: GateResolverFn) -> None:
        """Resolve (or schedule resolution of) every newly-open gate per the script."""
        script = self._script
        for gate in open_gates:
            if gate.gateId in self._handled:
                continue
            self._handled.add(gate.gateId)

            if script.mode == "auto_approve":
                if self._approved_count >= script.maxGates:
                    continue  # stop resolving → deadlock surfaces
                self._approved_count += 1
                await self._resolve(
                    gate.gateId,
                    GateResolutionWire(kind="approve"),
                    "auto",
                    "approve",
                    None,
                    script.afterSim,
                    resolve_now,
                )
                continue

            if script.mode == "auto_reject":
                await self._resolve(
                    gate.gateId,
                    GateResolutionWire(kind="reject", reason=script.reason),
                    "auto",
                    "reject",
                    script.reason,
                    None,
                    resolve_now,
                )
                continue

            # scripted
            state = self._find_scripted_step(gate)
            if state is not None:
                r = state.step.resolve
                await self._resolve(
                    gate.gateId,
                    _to_wire(r),
                    state.step.id,
                    r.kind,
                    r.reason if r.kind == "reject" else None,
                    state.step.afterSim,
                    resolve_now,
                )
                continue

            self._unexpected.append({"gateId": gate.gateId, "kind": gate.kind})
            if script.onUnexpectedGate != "fail_scenario":
                r = script.onUnexpectedGate.resolve
                await self._resolve(
                    gate.gateId,
                    _to_wire(r),
                    "unexpected",
                    r.kind,
                    r.reason if r.kind == "reject" else "unexpected gate",
                    None,
                    resolve_now,
                )
            # fail_scenario: recorded, NOT resolved — the deadlock (or the grader) surfaces it.

    def next_resolution_at(self) -> Optional[datetime]:
        """Earliest scheduled (sim-latency) resolution, or None."""
        return self._scheduled[0].at if self._scheduled else None

    async def resolve_due(self, now: datetime, resolve_now: GateResolverFn) -> None:
        """Fire every scheduled resolution due at `now`."""
        while self._scheduled and self._scheduled[0].at <= now:
            entry = self._scheduled.pop(0)
            await resolve_now(entry.gate_id, entry.wire, "harness:%s" % entry.step_label, entry.reason)
            self._resolutions.append(
                GateScriptResolution(
                    gateId=entry.gate_id, stepId=entry.step_label, resolution=entry.resolution_kind
                )
            )

    def report(self) -> GateScriptReport:
        never_raised = (
            [s.step.id for s in self._steps if not s.step.optional and s.fires == 0]
            if self._script.mode == "scripted"
            else []
        )
        return GateScriptReport(
            neverRaised=never_raised,
            unexpected=list(self._unexpected),
            resolutions=list(self._resolutions),
        )


def create_gate_script_engine(script: Any, clock: ClockPort, seed: int) -> GateScriptEngine:
    return GateScriptEngine(script, clock, seed)
