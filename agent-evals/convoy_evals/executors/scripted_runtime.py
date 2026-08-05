"""ScriptedRuntime -- a cooperative-coroutine RuntimeClient for scripted
executors (golden / violators). Implements the same RuntimeClient seam the
real agent-runtime will implement, so the harness drives both identically.

Port of src/executors/scripted-runtime.ts.

Scheduling model: the executor fn runs as an ordinary asyncio coroutine whose
awaits on ctx.wait / ctx.await_inbound / ctx.raise_gate "park" it on a
condition (an asyncio Future). drain() is the ONLY thing that advances the
coroutine: it scans parked entries, settles every condition currently
satisfiable, yields the event loop so the coroutine runs until it parks
again, and repeats until a scan settles nothing. No wall-clock sleeps
anywhere -- the sandbox advances the SimClock between drains.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from ..runtime.events import GateId, MissionId
from ..runtime.ports import (
    ClockPort,
    DrainReport,
    GateResolutionWire,
    OpenGate,
    ResolveGateInput,
    RuntimeClient,
    StartMissionInput,
)
from ..sandbox.api import ExecutorCtx, RuntimeFactory, ScriptedExecutorFn, WorldMessage

RESUME_CAP_PER_DRAIN = 10_000
# Event-loop yields per tick: enough to flush chained future resolutions even
# when gateway handlers themselves suspend a few times.
_TICK_SPINS = 50


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_ts(ts: Any) -> Optional[datetime]:
    """Date.parse-alike: None on anything unparseable (comparisons then fail)."""
    if not isinstance(ts, str) or not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _executor_source_hash(executor: ScriptedExecutorFn) -> str:
    try:
        source = inspect.getsource(executor)
    except (OSError, TypeError):
        source = repr(executor)
    return _sha256(source)


async def _tick() -> None:
    """Let the started coroutine(s) run until they park again."""
    for _ in range(_TICK_SPINS):
        await asyncio.sleep(0)


class _Parked:
    __slots__ = ("id", "kind", "fire_at", "ready", "settle")

    def __init__(
        self,
        id: str,
        kind: str,  # 'timer' | 'inbound' | 'gate'
        ready: Callable[[], bool],
        settle: Callable[[], None],
        fire_at: Optional[datetime] = None,
    ) -> None:
        self.id = id
        self.kind = kind
        self.ready = ready
        self.settle = settle
        # Set for timers only -- feeds DrainReport.nextTimerAt.
        self.fire_at = fire_at


class _MissionState:
    def __init__(self, mission_id: MissionId) -> None:
        self.mission_id = mission_id
        self.terminal: Optional[str] = None
        self.parked: List[_Parked] = []
        self.consumed_inbound: set = set()
        self.open_gates: Dict[GateId, OpenGate] = {}
        self.gate_resolutions: Dict[GateId, GateResolutionWire] = {}
        self.task: Optional[Any] = None


class _Ctx(ExecutorCtx):
    """Concrete ExecutorCtx bound to one mission of a scripted runtime."""

    def __init__(self, runtime: "_ScriptedRuntimeClient", state: _MissionState, input: StartMissionInput) -> None:
        self._runtime = runtime
        self._state = state
        self.missionId = state.mission_id
        self.clock = runtime._clock
        self.gateway = runtime._gateway
        self.log = runtime._log
        self.goal = input.goal
        self.params = input.params if input.params is not None else {}

    async def wait(self, duration_ms: float) -> None:
        state = self._state
        clock = self.clock
        log = self.log
        mission_id = self.missionId
        timer_id = str(uuid.uuid4())
        fire_at = clock.now() + timedelta(milliseconds=duration_ms)
        log.append(
            {
                "type": "timer_scheduled",
                "missionId": mission_id,
                "timerId": timer_id,
                "fireAt": _iso(fire_at),
            }
        )
        fut = asyncio.get_running_loop().create_future()

        def settle() -> None:
            log.append({"type": "timer_fired", "missionId": mission_id, "timerId": timer_id})
            fut.set_result(None)

        state.parked.append(
            _Parked(
                id=timer_id,
                kind="timer",
                ready=lambda: clock.now() >= fire_at,
                settle=settle,
                fire_at=fire_at,
            )
        )
        await fut

    async def await_inbound(
        self,
        to_contains: Optional[str] = None,
        subject_regex: Optional[str] = None,
        after_ts: Optional[str] = None,
    ) -> WorldMessage:
        state = self._state
        world = self._runtime._world
        after_dt = _parse_ts(after_ts) if after_ts is not None else None

        def find() -> Optional[WorldMessage]:
            candidates = []
            for m in world.list_messages(direction="inbound"):
                if m.id in state.consumed_inbound:
                    continue
                if to_contains is not None:
                    # Match recipients OR sender (reply-style matching).
                    if not (any(to_contains in t for t in m.to) or to_contains in m.from_):
                        continue
                if subject_regex is not None and re.search(subject_regex, m.subject) is None:
                    continue
                ts = _parse_ts(m.ts)
                if after_ts is not None:
                    if ts is None or after_dt is None or not (ts > after_dt):
                        continue
                candidates.append((ts, m))
            candidates.sort(key=lambda pair: pair[0] if pair[0] is not None else datetime.min.replace(tzinfo=timezone.utc))
            return candidates[0][1] if candidates else None

        fut = asyncio.get_running_loop().create_future()

        def settle() -> None:
            m = find()
            if m is None:
                raise RuntimeError("await_inbound settled without a matching message")
            state.consumed_inbound.add(m.id)
            fut.set_result(m)

        state.parked.append(
            _Parked(
                id=str(uuid.uuid4()),
                kind="inbound",
                ready=lambda: find() is not None,
                settle=settle,
            )
        )
        return await fut

    async def raise_gate(
        self,
        kind: str,
        payload: Any,
        step_tag: Optional[str] = None,
        item_ref: Optional[str] = None,
    ) -> Dict[str, Any]:
        state = self._state
        runtime = self._runtime
        mission_id = self.missionId
        gate_id = str(uuid.uuid4())
        event: Dict[str, Any] = {
            "type": "gate_raised",
            "missionId": mission_id,
            "gateId": gate_id,
            "kind": kind,
            "payload": payload,
            "deadlineAt": None,
        }
        if step_tag is not None:
            event["stepTag"] = step_tag
        if item_ref is not None:
            event["itemRef"] = item_ref
        self.log.append(event)

        state.open_gates[gate_id] = OpenGate(
            gateId=gate_id,
            kind=kind,
            deadlineAt=None,
            payload=payload,
            stepTag=step_tag,
        )
        runtime._gate_to_mission[gate_id] = state
        if item_ref is not None:
            runtime._gate_item_refs[gate_id] = item_ref

        fut = asyncio.get_running_loop().create_future()

        def settle() -> None:
            r = state.gate_resolutions.get(gate_id)
            if r is None:
                raise RuntimeError("gate {0} settled without a resolution".format(gate_id))
            if r.kind == "provide_input":
                payload_out: Any = r.payload
            elif r.kind == "edit_then_approve":
                payload_out = r.patch
            else:
                payload_out = None
            fut.set_result({"resolution": r.kind, "payload": payload_out})

        state.parked.append(
            _Parked(
                id=gate_id,
                kind="gate",
                ready=lambda: gate_id in state.gate_resolutions,
                settle=settle,
            )
        )
        return await fut

    def emit_artifact(
        self, tag: str, content: str, mime: str = "text/plain", item_ref: Optional[str] = None
    ) -> str:
        world = self._runtime._world
        file = world.put_file(name=tag, mime=mime, content=content)
        event: Dict[str, Any] = {
            "type": "artifact_created",
            "missionId": self.missionId,
            "artifactId": file.id,
            "hash": file.hash,
            "tag": tag,
            "mime": mime,
            "bytes": len(content.encode("utf-8")),
        }
        if item_ref is not None:
            event["itemRef"] = item_ref
        self.log.append(event)
        # Improvement over the TS port: return the file id directly so
        # executors don't need to scan the log for their own artifact.
        return file.id

    def land(self, summary: Optional[str] = None) -> None:
        self._runtime._record_terminal(self._state, "landed", summary if summary is not None else "landed")

    def fail(self, reason: str) -> None:
        self._runtime._record_terminal(self._state, "failed", reason)


class _ScriptedRuntimeClient(RuntimeClient):
    def __init__(self, executor: ScriptedExecutorFn, env: Dict[str, Any]) -> None:
        self._executor = executor
        self._gateway = env["gateway"]
        self._log = env["log"]
        self._world = env["world"]
        self._clock: ClockPort = env["clock"]
        self._missions: Dict[MissionId, _MissionState] = {}
        self._gate_to_mission: Dict[GateId, _MissionState] = {}
        self._gate_item_refs: Dict[GateId, str] = {}

    def _record_terminal(self, state: _MissionState, status: str, summary: Optional[str]) -> None:
        if state.terminal is not None:
            return
        state.terminal = status
        event: Dict[str, Any] = {
            "type": "terminal_outcome",
            "missionId": state.mission_id,
            "status": status,
            "judgedBy": "agent",
        }
        if summary is not None:
            event["summary"] = summary
        self._log.append(event)

    async def start_mission(self, input: StartMissionInput, clock: ClockPort) -> MissionId:
        mission_id = str(uuid.uuid4())
        state = _MissionState(mission_id)
        self._missions[mission_id] = state
        spec: Dict[str, Any] = {
            "modelId": "scripted",
            "promptHashes": {"executor": _executor_source_hash(self._executor)},
        }
        if input.params is not None:
            spec["params"] = input.params
        self._log.append(
            {
                "type": "mission_started",
                "missionId": mission_id,
                "missionType": input.missionType,
                "environmentId": input.environmentId,
                "goal": input.goal,
                "spec": spec,
            }
        )
        ctx = _Ctx(self, state, input)

        async def run() -> None:
            try:
                await self._executor(ctx)
            except Exception as err:  # noqa: BLE001 -- executor failure => failed outcome
                self._record_terminal(state, "failed", str(err))
            else:
                if state.terminal is None:
                    self._record_terminal(state, "landed", "executor returned")

        # Start the coroutine; do NOT await -- drain() drives it. start_mission
        # is async, so a loop is guaranteed to be running here.
        state.task = asyncio.ensure_future(run())
        return mission_id

    def _scan_and_settle(self, state: _MissionState) -> int:
        """Settle every parked entry whose condition holds right now."""
        ready = [p for p in state.parked if p.ready()]
        if not ready:
            return 0
        ready_set = set(id(p) for p in ready)
        state.parked = [p for p in state.parked if id(p) not in ready_set]
        for p in ready:
            p.settle()
        return len(ready)

    def _build_report(self, state: _MissionState, steps_executed: int) -> DrainReport:
        next_timer_at: Optional[datetime] = None
        for p in state.parked:
            if p.kind != "timer" or p.fire_at is None:
                continue
            if next_timer_at is None or p.fire_at < next_timer_at:
                next_timer_at = p.fire_at
        return DrainReport(
            terminal=state.terminal,
            openGates=list(state.open_gates.values()),
            nextTimerAt=next_timer_at,
            stepsExecuted=steps_executed,
        )

    async def drain(self, mission_id: MissionId, clock: ClockPort) -> DrainReport:
        state = self._missions.get(mission_id)
        if state is None:
            raise RuntimeError("drain: unknown missionId {0}".format(mission_id))
        resumes = 0
        # Initial flush: let a just-started (or just-resolved) coroutine run
        # to its park point.
        await _tick()
        while True:
            settled = self._scan_and_settle(state)
            resumes += settled
            if resumes > RESUME_CAP_PER_DRAIN:
                raise RuntimeError(
                    "drain: resume cap ({0}) exceeded for mission {1} -- runaway executor loop?".format(
                        RESUME_CAP_PER_DRAIN, mission_id
                    )
                )
            if settled == 0:
                break
            await _tick()
        return self._build_report(state, resumes)

    async def resolve_gate(self, input: ResolveGateInput, clock: ClockPort) -> None:
        state = self._gate_to_mission.get(input.gateId)
        if state is None:
            raise RuntimeError("resolve_gate: unknown gateId {0}".format(input.gateId))
        r = input.resolution
        raised_item_ref = self._gate_item_refs.get(input.gateId)
        event: Dict[str, Any] = {
            "type": "gate_resolved",
            "missionId": state.mission_id,
            "gateId": input.gateId,
            "resolution": r.kind,
            "resolvedBy": input.resolvedBy,
        }
        # Resolution inherits the raising gate's item attribution so
        # item-scoped trajectory graders see the full gate lifecycle.
        if raised_item_ref is not None:
            event["itemRef"] = raised_item_ref
        if input.reason is not None:
            event["reason"] = input.reason
        if r.kind == "edit_then_approve":
            event["patch"] = r.patch
        self._log.append(event)
        state.gate_resolutions[input.gateId] = r
        state.open_gates.pop(input.gateId, None)
        # The parked gate entry is now ready(); the next drain() settles it.


def create_scripted_runtime_factory(executor: ScriptedExecutorFn) -> RuntimeFactory:
    def factory(env: Dict[str, Any]) -> RuntimeClient:
        return _ScriptedRuntimeClient(executor, env)

    return factory
