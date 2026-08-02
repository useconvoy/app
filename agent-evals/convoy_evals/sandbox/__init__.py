"""Sandbox — public surface. Everything the runner needs to build and drive a
simulated environment for one scenario run. Port of src/sandbox/index.ts."""

from .api import (
    GateScriptReport,
    GateScriptResolution,
    GradeRecord,
    RunReport,
    RuntimeFactory,
    SandboxInstance,
    SandboxService,
    SimClock,
    StopCondition,
    ToolCallCtx,
    ToolEmulator,
    ToolGateway,
    WorldAttachment,
    WorldBundle,
    WorldEvent,
    WorldFile,
    WorldMessage,
    WorldRecord,
    WorldStore,
)
from .clock import create_sim_clock
from .counterparty import (
    CounterpartyEngine,
    carrier_slug,
    create_counterparty_engine,
    expand_profile,
    mulberry32,
)
from .emulators.ams import ams_emulator
from .emulators.calendar import calendar_emulator
from .emulators.carrier_portal import carrier_portal_emulator
from .emulators.email import email_emulator
from .emulators.util import AGENT_ADDRESS, thread_id_for_subject, world_clock
from .gates import GateResolverFn, GateScriptEngine, create_gate_script_engine
from .gateway import create_tool_gateway
from .instance import DEFAULT_PACK_ROOT, create_sandbox_service, default_emulators
from .world import (
    SimWorldStore,
    create_world_store,
    materialize_deep,
    materialize_string,
    sha256_hex,
    stable_stringify,
)

__all__ = [
    "AGENT_ADDRESS",
    "CounterpartyEngine",
    "DEFAULT_PACK_ROOT",
    "GateResolverFn",
    "GateScriptEngine",
    "GateScriptReport",
    "GateScriptResolution",
    "GradeRecord",
    "RunReport",
    "RuntimeFactory",
    "SandboxInstance",
    "SandboxService",
    "SimClock",
    "SimWorldStore",
    "StopCondition",
    "ToolCallCtx",
    "ToolEmulator",
    "ToolGateway",
    "WorldAttachment",
    "WorldBundle",
    "WorldEvent",
    "WorldFile",
    "WorldMessage",
    "WorldRecord",
    "WorldStore",
    "ams_emulator",
    "calendar_emulator",
    "carrier_portal_emulator",
    "carrier_slug",
    "create_counterparty_engine",
    "create_gate_script_engine",
    "create_sandbox_service",
    "create_sim_clock",
    "create_tool_gateway",
    "create_world_store",
    "default_emulators",
    "email_emulator",
    "expand_profile",
    "materialize_deep",
    "materialize_string",
    "mulberry32",
    "sha256_hex",
    "stable_stringify",
    "thread_id_for_subject",
    "world_clock",
]
