"""ToolGateway — the only way any executor touches the world.

Port of src/sandbox/gateway.ts. Enforces the effect-class rule: pure tools
emit one collapsed `tool_call` event; effectful tools go through the two-phase
envelope (intent → approved → executed → result) with idempotency-key dedupe
(crash-replay safety: a re-invoke with the same key returns the recorded
result without re-running the handler, logging intent + result only). Every
invoke appends a flat budget_debit (v1 metering).
"""

from __future__ import annotations

import hashlib
import inspect
from typing import Any, Dict, List, Optional

from ..runtime.log import EventLog
from .api import ToolCallCtx, ToolEmulator, ToolGateway, WorldStore
from .world import stable_stringify

FLAT_TOOL_DEBIT_USD = 0.001


def _binding_kind(binding: Any) -> Optional[str]:
    if binding is None:
        return None
    if isinstance(binding, dict):
        return binding.get("kind")
    return getattr(binding, "kind", None)


class _ToolGateway(ToolGateway):
    def __init__(
        self,
        emulators: List[ToolEmulator],
        bindings: Dict[str, Any],
        log: EventLog,
        world: WorldStore,
        mission_budget_usd: Optional[float] = None,
    ) -> None:
        self._bindings = bindings or {}
        self._log = log
        self._world = world
        self._mission_budget_usd = mission_budget_usd
        self._by_tool: Dict[str, ToolEmulator] = {}
        for em in emulators:
            if em.tool in self._by_tool:
                raise ValueError("duplicate tool emulator registered: %s" % em.tool)
            self._by_tool[em.tool] = em
        # idempotencyKey → recorded successful result (errors are NOT recorded: retries re-run).
        self._executed: Dict[str, Dict[str, Any]] = {}
        # Deterministic per-gateway counter so auto-minted keys never collide.
        self._invoke_seq = 0

    @staticmethod
    def _base(ctx: ToolCallCtx) -> Dict[str, Any]:
        return {"missionId": ctx.missionId, "itemRef": ctx.itemRef, "stepId": ctx.stepId}

    def _debit(self, ctx: ToolCallCtx) -> None:
        base = self._base(ctx)
        base.update({"type": "budget_debit", "usd": FLAT_TOOL_DEBIT_USD, "resource": "tool"})
        self._log.append(base)

    async def _run_handler(self, emulator: ToolEmulator, args: Any, ctx: ToolCallCtx) -> Any:
        result = emulator.handler(args, self._world, ctx)
        if inspect.isawaitable(result):
            result = await result
        return result

    async def invoke(self, tool: str, args: Any, ctx: ToolCallCtx) -> Any:
        emulator = self._by_tool.get(tool)
        if not emulator:
            raise ValueError("unknown tool: %s" % tool)
        kind = _binding_kind(self._bindings.get(tool))
        if kind is not None and kind != "emulator":
            raise ValueError(
                "binding kind '%s' for tool '%s' not implemented in v1" % (kind, tool)
            )

        base = self._base(ctx)

        if not emulator.effectful:
            # Pure/read tool: one collapsed event, result (or error) inline.
            try:
                result = await self._run_handler(emulator, args, ctx)
            except Exception as err:
                event = dict(base)
                event.update({"type": "tool_call", "tool": tool, "args": args, "error": str(err)})
                self._log.append(event)
                self._debit(ctx)
                raise
            event = dict(base)
            event.update({"type": "tool_call", "tool": tool, "args": args, "result": result})
            self._log.append(event)
            self._debit(ctx)
            return result

        # Effectful tool: two-phase envelope + idempotency dedupe. The key is
        # CALLER-minted (per attempt): same key ⇒ dedupe (crash replay never
        # re-fires); no key ⇒ fresh per invoke, so intentional retries re-run.
        idempotency_key = ctx.idempotencyKey
        if idempotency_key is None:
            seed = "%s\n%s\n%s\n%d" % (tool, stable_stringify(args), ctx.missionId, self._invoke_seq)
            self._invoke_seq += 1
            idempotency_key = hashlib.sha256(seed.encode("utf-8")).hexdigest()

        intent = dict(base)
        intent.update(
            {"type": "tool_intent", "tool": tool, "args": args, "idempotencyKey": idempotency_key}
        )
        self._log.append(intent)

        prior = self._executed.get(idempotency_key)
        if prior is not None:
            # Dedupe: return the recorded result WITHOUT re-running the handler.
            event = dict(base)
            event.update(
                {
                    "type": "tool_result",
                    "tool": tool,
                    "idempotencyKey": idempotency_key,
                    "result": prior["result"],
                }
            )
            self._log.append(event)
            self._debit(ctx)
            return prior["result"]

        approved = dict(base)
        approved.update({"type": "tool_approved", "tool": tool, "idempotencyKey": idempotency_key})
        self._log.append(approved)
        try:
            result = await self._run_handler(emulator, args, ctx)
        except Exception as err:
            event = dict(base)
            event.update(
                {
                    "type": "tool_result",
                    "tool": tool,
                    "idempotencyKey": idempotency_key,
                    "error": str(err),
                }
            )
            self._log.append(event)
            self._debit(ctx)
            raise
        executed = dict(base)
        executed.update(
            {"type": "tool_executed", "tool": tool, "idempotencyKey": idempotency_key, "args": args}
        )
        self._log.append(executed)
        res_event = dict(base)
        res_event.update(
            {"type": "tool_result", "tool": tool, "idempotencyKey": idempotency_key, "result": result}
        )
        self._log.append(res_event)
        self._debit(ctx)
        self._executed[idempotency_key] = {"result": result}
        return result

    def manifest_hash(self) -> str:
        lines = sorted("%s:%s" % (e.tool, "true" if e.effectful else "false") for e in self._by_tool.values())
        return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def create_tool_gateway(
    emulators: List[ToolEmulator],
    bindings: Dict[str, Any],
    log: EventLog,
    world: WorldStore,
    mission_budget_usd: Optional[float] = None,
) -> ToolGateway:
    return _ToolGateway(emulators, bindings, log, world, mission_budget_usd)
