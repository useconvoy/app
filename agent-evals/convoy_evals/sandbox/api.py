"""Sandbox interfaces — the contract between the DES driver, world + emulators,
counterparty engine, gate-script engine, and whatever executor is evaluated.

Port of src/sandbox/api.ts. The executor cannot tell sim from prod: it sees a
ClockPort, a ToolGateway, and gates — nothing else.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional, Protocol, Union

from ..runtime.events import MissionId
from ..runtime.log import EventLog
from ..runtime.ports import ClockPort, OpenGate, RuntimeClient
from ..schema.scenario import Scenario

# --------------------------------------------------------------------------
# Clock
# --------------------------------------------------------------------------


class SimClock(ClockPort):
    """Harness-owned sim clock. Sole authority on sim time; only runUntil advances it."""

    @abc.abstractmethod
    def advance_to(self, t: datetime) -> None: ...


# --------------------------------------------------------------------------
# World
# --------------------------------------------------------------------------


@dataclass
class WorldAttachment:
    name: str
    fileId: str


@dataclass
class WorldMessage:
    id: str
    threadId: str
    from_: str
    to: List[str]
    subject: str
    body: str
    attachments: List[WorldAttachment]
    ts: str  # domain-time ISO
    direction: Literal["outbound", "inbound"]  # relative to the agent


@dataclass
class WorldRecord:
    collection: str
    id: str
    fields: Dict[str, Any]
    updatedAt: str


@dataclass
class WorldFile:
    id: str
    name: str
    mime: str
    hash: str  # sha256 hex of content
    content: str


WorldEvent = Dict[str, Any]  # {'kind': 'message_sent'|'message_delivered'|'record_changed'|'portal_transition', ...}


class WorldStore(abc.ABC):
    """In-process world state one sandbox instance owns. Mutations emit
    WorldEvent on the bus; content exports as a content-addressed bundle."""

    @abc.abstractmethod
    def seed_from_pack(self, pack_dir: str, t0: datetime, seed: int) -> None: ...

    @abc.abstractmethod
    def send_message(self, **kw: Any) -> WorldMessage: ...  # outbound (agent → world)

    @abc.abstractmethod
    def deliver_message(self, **kw: Any) -> WorldMessage: ...  # inbound (world → agent)

    @abc.abstractmethod
    def list_messages(
        self,
        direction: Optional[str] = None,
        to_contains: Optional[str] = None,
        thread_id: Optional[str] = None,
    ) -> List[WorldMessage]: ...

    @abc.abstractmethod
    def upsert_record(self, collection: str, id: str, fields: Dict[str, Any]) -> WorldRecord: ...

    @abc.abstractmethod
    def get_record(self, collection: str, id: str) -> Optional[WorldRecord]: ...

    @abc.abstractmethod
    def list_records(self, collection: str) -> List[WorldRecord]: ...

    @abc.abstractmethod
    def put_file(self, name: str, mime: str, content: str) -> WorldFile: ...

    @abc.abstractmethod
    def get_file(self, id: str) -> Optional[WorldFile]: ...

    @abc.abstractmethod
    def file_by_pack_path(self, path: str) -> Optional[WorldFile]: ...

    @abc.abstractmethod
    def on_event(self, handler: Callable[[WorldEvent], None]) -> None: ...

    @abc.abstractmethod
    def emit_event(self, event: WorldEvent) -> None: ...

    @abc.abstractmethod
    def query(self, q: str) -> List[Any]:
        """Dotted-path read over {records.{coll}.{id}: fields, messages.{sent,inbound,all}, files.{id}}."""

    @abc.abstractmethod
    def export_bundle(self) -> "WorldBundle": ...


@dataclass
class WorldBundle:
    hash: str
    messages: List[WorldMessage]
    records: List[WorldRecord]
    files: List[WorldFile]

    def to_json(self) -> Dict[str, Any]:
        from dataclasses import asdict

        d = asdict(self)
        for m in d["messages"]:
            m["from"] = m.pop("from_")
        return d

    @classmethod
    def from_json(cls, d: Dict[str, Any]) -> "WorldBundle":
        msgs = []
        for m in d.get("messages", []):
            m = dict(m)
            m["from_"] = m.pop("from", m.pop("from_", ""))
            m["attachments"] = [WorldAttachment(**a) for a in m.get("attachments", [])]
            msgs.append(WorldMessage(**m))
        return cls(
            hash=d.get("hash", ""),
            messages=msgs,
            records=[WorldRecord(**r) for r in d.get("records", [])],
            files=[WorldFile(**f) for f in d.get("files", [])],
        )


# --------------------------------------------------------------------------
# Tool gateway — the only way any executor touches the world
# --------------------------------------------------------------------------


@dataclass
class ToolCallCtx:
    missionId: MissionId
    itemRef: Optional[str] = None
    stepId: Optional[str] = None
    # Caller-minted idempotency key: same key ⇒ dedupe (crash replay never
    # re-fires); omitted ⇒ fresh key per invoke, so intentional retries re-run.
    idempotencyKey: Optional[str] = None


class ToolGateway(abc.ABC):
    @abc.abstractmethod
    async def invoke(self, tool: str, args: Any, ctx: ToolCallCtx) -> Any: ...

    @abc.abstractmethod
    def manifest_hash(self) -> str: ...


@dataclass
class ToolEmulator:
    tool: str
    effectful: bool
    # handler(args, world, ctx) -> result (sync or async)
    handler: Callable[[Any, WorldStore, ToolCallCtx], Any]


# --------------------------------------------------------------------------
# Sandbox instance + DES driver
# --------------------------------------------------------------------------

StopCondition = Dict[str, Any]  # {'kind': 'terminal'|'gate-open'|'sim-time', 'at'?: datetime}


@dataclass
class RunReport:
    terminal: Optional[str]
    deadlock: bool
    guardTripped: Optional[str]  # 'wall' | 'usd' | 'sim' | None
    openGates: List[OpenGate]
    simNow: datetime
    wallMs: float
    stepsExecuted: int
    deadlockDiagnosis: Optional[str] = None


@dataclass
class GateScriptResolution:
    gateId: str
    stepId: str  # scripted step id | 'auto' | 'unexpected'
    resolution: str


@dataclass
class GateScriptReport:
    neverRaised: List[str] = field(default_factory=list)
    unexpected: List[Dict[str, str]] = field(default_factory=list)
    resolutions: List[GateScriptResolution] = field(default_factory=list)


class SandboxInstance(abc.ABC):
    id: str
    environmentId: str
    clock: SimClock
    world: WorldStore
    log: EventLog
    missionId: MissionId

    @abc.abstractmethod
    async def run_until(self, stop: StopCondition) -> RunReport:
        """The DES driver: drain → apply gate scripts → advance to next event → deliver."""

    @abc.abstractmethod
    def gate_report(self) -> GateScriptReport: ...

    @abc.abstractmethod
    def destroy(self) -> Dict[str, Any]:
        """Returns {'events': [...], 'world': WorldBundle}."""


# The runtime under evaluation is constructed AFTER the sandbox exists (it
# needs the instance's gateway/log/world/clock injected), so the sandbox takes
# a factory.
RuntimeFactory = Callable[[Dict[str, Any]], RuntimeClient]  # env: gateway/log/world/clock


class SandboxService(abc.ABC):
    @abc.abstractmethod
    async def create(
        self,
        scenario: Scenario,
        runtime_factory: RuntimeFactory,
        pack_dir: Optional[str] = None,
    ) -> SandboxInstance: ...


# --------------------------------------------------------------------------
# Grading record — everything graders may see. No transcript, no model text.
# --------------------------------------------------------------------------


@dataclass
class GradeRecord:
    scenario: Scenario
    answerKey: Any  # schema.scenario.AnswerKey
    events: List[Any]  # ConvoyEvent models
    world: WorldBundle
    worldQuery: Callable[[str], List[Any]]
    gateReport: GateScriptReport


# --------------------------------------------------------------------------
# Scripted executors — asyncio coroutines scheduled by the ScriptedRuntime
# --------------------------------------------------------------------------


class ExecutorCtx(abc.ABC):
    missionId: MissionId
    clock: ClockPort
    gateway: ToolGateway
    log: EventLog
    goal: str
    params: Dict[str, Any]

    @abc.abstractmethod
    async def wait(self, duration_ms: float) -> None:
        """Park until sim time ≥ now + duration (registers a durable timer)."""

    @abc.abstractmethod
    async def await_inbound(
        self,
        to_contains: Optional[str] = None,
        subject_regex: Optional[str] = None,
        after_ts: Optional[str] = None,
    ) -> WorldMessage: ...

    @abc.abstractmethod
    async def raise_gate(
        self,
        kind: str,
        payload: Any,
        step_tag: Optional[str] = None,
        item_ref: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Raise a gate and park until resolved → {'resolution': kind, 'payload': ...}."""

    @abc.abstractmethod
    def emit_artifact(
        self, tag: str, content: str, mime: str = "text/plain", item_ref: Optional[str] = None
    ) -> str:
        """Record an artifact; returns the artifact's world file id."""

    @abc.abstractmethod
    def land(self, summary: Optional[str] = None) -> None: ...

    @abc.abstractmethod
    def fail(self, reason: str) -> None: ...


ScriptedExecutorFn = Callable[[ExecutorCtx], Awaitable[None]]
