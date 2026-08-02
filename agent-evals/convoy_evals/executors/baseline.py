"""FROZEN — control arm; do not extend.

Naive long-context baseline: one system prompt, one flat message list, a
single tool-use loop over the raw Anthropic Messages API (raw HTTP POST, no
SDK). No planning, no memory, no gates, no chase cadence -- whatever the model
does with the raw tools is the control measurement. Env-gated on
ANTHROPIC_API_KEY; no tests (never runs in CI).

Port of src/executors/baseline.ts.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from datetime import timezone
from typing import Any, Dict, List, Optional

from ..runtime.events import MissionId
from ..runtime.ports import (
    ClockPort,
    DrainReport,
    ResolveGateInput,
    RuntimeClient,
    StartMissionInput,
)
from ..sandbox.api import RuntimeFactory, ToolCallCtx, ToolGateway

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODEL_ID = "claude-sonnet-4-6"
# Pricing constants for the control arm -- $3/M input, $15/M output.
USD_PER_INPUT_TOKEN = 3 / 1_000_000
USD_PER_OUTPUT_TOKEN = 15 / 1_000_000
MAX_TURNS = 100
MAX_TOKENS_PER_TURN = 4096

# Anthropic tool names may not contain '.'; expose 'email.send' as 'email__send'.
TOOL_NAMES = [
    "email.send",
    "email.list_inbox",
    "ams.get_policy",
    "ams.update_policy",
    "portal.request_loss_runs",
    "portal.check_status",
    "portal.download",
    "calendar.create_event",
    "calendar.list_events",
]


def _wire_name(tool: str) -> str:
    return tool.replace(".", "__")


def _real_name(wire: str) -> str:
    return wire.replace("__", ".")


async def _post_json(url: str, headers: Dict[str, str], payload: Dict[str, Any]) -> Dict[str, Any]:
    """Raw POST: httpx when available (async), stdlib urllib otherwise.

    Returns {'status': int, 'body': dict}.
    """
    try:
        import httpx  # type: ignore
    except ImportError:
        httpx = None

    if httpx is not None:
        async with httpx.AsyncClient(timeout=600.0) as client:
            res = await client.post(url, headers=headers, json=payload)
            try:
                body = res.json()
            except ValueError:
                body = {}
            return {"status": res.status_code, "body": body if isinstance(body, dict) else {}}

    import urllib.error
    import urllib.request

    def blocking_post() -> Dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                raw = resp.read().decode("utf-8")
                status = resp.status
        except urllib.error.HTTPError as err:
            raw = err.read().decode("utf-8")
            status = err.code
        try:
            body = json.loads(raw)
        except ValueError:
            body = {}
        return {"status": status, "body": body if isinstance(body, dict) else {}}

    return await asyncio.get_running_loop().run_in_executor(None, blocking_post)


class _BaselineMission:
    def __init__(self, input: StartMissionInput) -> None:
        self.input = input
        self.messages: List[Any] = [
            {
                "role": "user",
                "content": "Begin the mission. Mission params: {0}".format(
                    json.dumps(input.params if input.params is not None else {})
                ),
            }
        ]
        self.turns = 0
        self.terminal: Optional[str] = None


class _BaselineRuntimeClient(RuntimeClient):
    def __init__(self, model_id: Optional[str], env: Dict[str, Any]) -> None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "baseline executor: ANTHROPIC_API_KEY is not set. The frozen naive baseline calls the "
                "real Anthropic API -- export ANTHROPIC_API_KEY, or run a scripted executor instead."
            )
        self._api_key = api_key
        self._model_id = model_id if model_id is not None else DEFAULT_MODEL_ID
        self._gateway: ToolGateway = env["gateway"]
        self._log = env["log"]
        self._clock: ClockPort = env["clock"]
        self._missions: Dict[MissionId, _BaselineMission] = {}
        self._tools = [
            {
                "name": _wire_name(tool),
                "description": "Invoke the {0} tool of the mission environment. Pass arguments as a JSON object.".format(
                    tool
                ),
                "input_schema": {"type": "object"},
            }
            for tool in TOOL_NAMES
        ]

    async def _call_model(self, system: str, messages: List[Any]) -> Dict[str, Any]:
        res = await _post_json(
            API_URL,
            headers={
                "content-type": "application/json",
                "x-api-key": self._api_key,
                "anthropic-version": API_VERSION,
            },
            payload={
                "model": self._model_id,
                "max_tokens": MAX_TOKENS_PER_TURN,
                "system": system,
                "tools": self._tools,
                "messages": messages,
            },
        )
        body = res["body"]
        if res["status"] < 200 or res["status"] >= 300:
            error = body.get("error") if isinstance(body.get("error"), dict) else {}
            raise RuntimeError(
                "baseline: Anthropic API {0}: {1}".format(res["status"], error.get("message", "unknown error"))
            )
        return body

    async def _run_tool(self, mission_id: MissionId, block: Dict[str, Any]) -> Dict[str, Any]:
        tool_use_id = block.get("id") or str(uuid.uuid4())
        try:
            result = await self._gateway.invoke(
                _real_name(block.get("name") or ""),
                block.get("input") if block.get("input") is not None else {},
                ToolCallCtx(missionId=mission_id),
            )
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": json.dumps(result if result is not None else None),
            }
        except Exception as err:  # noqa: BLE001 -- surface tool errors to the model
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": str(err),
                "is_error": True,
            }

    def _record_terminal(self, mission_id: MissionId, m: _BaselineMission, status: str, summary: str) -> None:
        if m.terminal is not None:
            return
        m.terminal = status
        self._log.append(
            {
                "type": "terminal_outcome",
                "missionId": mission_id,
                "status": status,
                "judgedBy": "agent",
                "summary": summary,
            }
        )

    def _system_prompt(self, m: _BaselineMission) -> str:
        return "{0}\n\ntoday is {1}".format(
            m.input.goal, self._clock.now().astimezone(timezone.utc).isoformat()[:10]
        )

    async def start_mission(self, input: StartMissionInput, clock: ClockPort) -> MissionId:
        mission_id = str(uuid.uuid4())
        m = _BaselineMission(input)
        self._missions[mission_id] = m
        system = self._system_prompt(m)
        spec: Dict[str, Any] = {
            "modelId": self._model_id,
            "promptHashes": {"system": hashlib.sha256(system.encode("utf-8")).hexdigest()},
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
        return mission_id

    async def drain(self, mission_id: MissionId, clock: ClockPort) -> DrainReport:
        m = self._missions.get(mission_id)
        if m is None:
            raise RuntimeError("baseline drain: unknown missionId {0}".format(mission_id))
        steps = 0

        while m.terminal is None and m.turns < MAX_TURNS:
            system = self._system_prompt(m)
            try:
                response = await self._call_model(system, m.messages)
            except Exception as err:  # noqa: BLE001 -- API failure => failed outcome
                self._record_terminal(mission_id, m, "failed", str(err))
                break
            m.turns += 1
            steps += 1

            usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
            tokens_in = usage.get("input_tokens") or 0
            tokens_out = usage.get("output_tokens") or 0
            cost_usd = tokens_in * USD_PER_INPUT_TOKEN + tokens_out * USD_PER_OUTPUT_TOKEN
            self._log.append(
                {
                    "type": "model_call",
                    "missionId": mission_id,
                    "modelId": self._model_id,
                    "tokensIn": tokens_in,
                    "tokensOut": tokens_out,
                    "costUsd": cost_usd,
                }
            )
            self._log.append(
                {
                    "type": "budget_debit",
                    "missionId": mission_id,
                    "usd": cost_usd,
                    "tokens": tokens_in + tokens_out,
                    "resource": "model",
                }
            )

            content = response.get("content") if isinstance(response.get("content"), list) else []
            m.messages.append({"role": "assistant", "content": content})

            if response.get("stop_reason") == "tool_use":
                tool_uses = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
                results = []
                for block in tool_uses:
                    results.append(await self._run_tool(mission_id, block))
                m.messages.append({"role": "user", "content": results})
                continue

            final_text = "\n".join(
                b.get("text") or ""
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )[:2000]
            self._record_terminal(
                mission_id,
                m,
                "landed",
                final_text or "stopped: {0}".format(response.get("stop_reason") or "unknown"),
            )

        if m.terminal is None and m.turns >= MAX_TURNS:
            self._record_terminal(mission_id, m, "failed", "baseline turn cap ({0}) reached".format(MAX_TURNS))

        return DrainReport(terminal=m.terminal, openGates=[], nextTimerAt=None, stepsExecuted=steps)

    async def resolve_gate(self, input: ResolveGateInput, clock: ClockPort) -> None:
        raise RuntimeError("baseline executor raises no gates; resolve_gate is not supported")


def create_baseline_runtime_factory(model_id: Optional[str] = None) -> RuntimeFactory:
    def factory(env: Dict[str, Any]) -> RuntimeClient:
        return _BaselineRuntimeClient(model_id, env)

    return factory
