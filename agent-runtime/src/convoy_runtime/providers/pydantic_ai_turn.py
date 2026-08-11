"""Real turn execution on Pydantic AI, behind the owned TurnExecutor seam.

One `execute_turn` call = one model interaction loop (model call + inline
tools) through the LiteLLM proxy using the run's virtual key. Everything the
loop does is recorded in a claim-checked transcript artifact: prompts, model
messages, every tool call with its result, rejections of unknown/unauthorized
tools, and any model fallback that occurred.

Policy-constrained fallback: the gateway resolves the agent's model to a chain
that only permutes within the deployment's approved models; transport-level
failures (connect errors, 5xx, 429) move to the next model and the result
records which model actually served the turn.
"""

import hashlib
import json
import re
from decimal import Decimal
from typing import Any

import httpx
from openai import AsyncOpenAI
from pydantic_ai import Agent, Tool
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    RetryPromptPart,
)
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from convoy_core import (
    ArtifactRef,
    EnvironmentBinding,
    TokenCounts,
    ToolCallRequest,
    ToolGrant,
    TurnInput,
    TurnResult,
)
from convoy_runtime.providers.artifact_store import ArtifactStore
from convoy_runtime.providers.context_assembly import render_pinned_header
from convoy_runtime.providers.grants import effective_tools
from convoy_runtime.providers.model_gateway import ModelGateway
from convoy_runtime.providers.model_keys import LiteLLMKeyProvider
from convoy_runtime.providers.promoted import (
    PROMOTED_TOOL_ACTIVITY,
    SANDBOX_JOB_ACTIVITY,
    SANDBOX_TOOL_PREFIX,
    promoted_call_key,
)
from convoy_runtime.providers.turn_executor import HeartbeatFn, TurnContext

# The model marks a step complete by including this token in its final text.
STEP_DONE_MARKER = "STEP_DONE"

COST_HEADER = "x-litellm-response-cost"

_SYSTEM_INSTRUCTIONS = (
    "You are a Convoy run agent executing one step of an approved plan.\n"
    "Use the available tools for any facts you need; never invent data.\n"
    f"When the current step is fully complete, include the exact token "
    f"{STEP_DONE_MARKER} in your final answer. If more work remains, answer "
    f"without that token.\n\n"
)


class PydanticAITurnExecutor:
    """TurnExecutor implementation on Pydantic AI over the LiteLLM proxy."""

    def __init__(
        self,
        *,
        store: ArtifactStore,
        gateway: ModelGateway,
        litellm_base_url: str,
        key_provider: LiteLLMKeyProvider,
        environments_internal_token: str = "",
        request_timeout_seconds: float = 60.0,
    ) -> None:
        if not litellm_base_url:
            raise ValueError("litellm_base_url is required")
        self._store = store
        self._gateway = gateway
        self._base_url = litellm_base_url.rstrip("/")
        self._keys = key_provider
        self._environments_internal_token = environments_internal_token
        self._timeout = request_timeout_seconds

    async def execute_turn(
        self,
        turn: TurnInput,
        ctx: TurnContext,
        heartbeat: HeartbeatFn | None = None,
    ) -> TurnResult:
        binding = EnvironmentBinding.model_validate(await self._store.get_json(ctx.binding_ref))
        header: dict[str, Any] = await self._store.get_json(turn.pinned_ref)
        effective = effective_tools(ctx.agent.tools, binding.tool_registry)
        data_plane_url = str(binding.connector_endpoints.get("data_plane", "")).rstrip("/")
        history = await self._load_history(turn)

        # The run's virtual key: provisioned at run start; ensuring here is an
        # idempotent no-op that also covers direct (non-workflow) callers.
        api_key = await self._keys.ensure_run_key(turn.run_id, turn.budget_remaining)

        tool_log: list[dict[str, Any]] = []
        costs: list[Decimal] = []
        pending_promoted: list[ToolCallRequest] = []
        resume_result: dict[str, Any] | None = None
        if ctx.resume is not None:
            resume_result = await self._store.get_json(ctx.resume.result_ref)

        async def _record_cost(response: httpx.Response) -> None:
            raw = response.headers.get(COST_HEADER)
            if raw:
                costs.append(Decimal(raw))

        async with httpx.AsyncClient(
            timeout=self._timeout, event_hooks={"response": [_record_cost]}
        ) as http_client:
            tools = self._build_tools(
                effective,
                data_plane_url=data_plane_url,
                turn=turn,
                ctx=ctx,
                tool_log=tool_log,
                heartbeat=heartbeat,
                http_client=http_client,
                pending_promoted=pending_promoted,
            )
            provider = OpenAIProvider(
                openai_client=AsyncOpenAI(
                    base_url=f"{self._base_url}/v1",
                    api_key=api_key,
                    http_client=http_client,
                    max_retries=0,  # fallback policy owns retries across models
                )
            )
            chain = self._gateway.resolve_chain(ctx.agent.model)
            result, model_used = await self._run_with_fallback(
                chain=chain,
                provider=provider,
                tools=tools,
                header=header,
                turn=turn,
                history=history,
                resume_result=resume_result,
            )

        messages = result.all_messages()
        self._record_rejections(messages, tool_log)
        output_text = str(result.output)
        promoted_call = pending_promoted[0] if pending_promoted else None
        outcome = (
            "promote"
            if promoted_call is not None
            else "step_done"
            if STEP_DONE_MARKER in output_text
            else "continue"
        )

        transcript_ref = await self._store.put_json(
            f"runs/{turn.run_id}/transcripts/{turn.step_id}/turn-{ctx.turn}.json",
            {
                "format": "pydantic-ai-messages",
                "run_id": turn.run_id,
                "step_id": turn.step_id,
                "turn": ctx.turn,
                "now": turn.now.isoformat(),
                "steers_drained": [steer.id for steer in turn.steers],
                "model_requested": ctx.agent.model,
                "model_used": model_used,
                "fallback": (
                    {"from": ctx.agent.model, "to": model_used}
                    if model_used != ctx.agent.model
                    else None
                ),
                "messages": json.loads(ModelMessagesTypeAdapter.dump_json(messages)),
                "tool_log": tool_log,
                "promote_requested": (
                    promoted_call.model_dump(mode="json") if promoted_call is not None else None
                ),
                "promoted_resume": (
                    {
                        "tool_id": ctx.resume.tool_id,
                        "idempotency_key": ctx.resume.idempotency_key,
                        "result_ref_key": ctx.resume.result_ref.key,
                        "result": resume_result,
                    }
                    if ctx.resume is not None
                    else None
                ),
                "output": output_text,
            },
        )

        step_outputs: list[ArtifactRef] = []
        if outcome == "step_done":
            step_outputs.append(
                await self._store.put_json(
                    f"runs/{turn.run_id}/outputs/{turn.step_id}.json",
                    {"step_id": turn.step_id, "response": output_text},
                )
            )

        usage = result.usage
        return TurnResult(
            transcript_ref=transcript_ref,
            tokens=TokenCounts(
                input_tokens=usage.input_tokens or 0,
                output_tokens=usage.output_tokens or 0,
            ),
            cost_usd=sum(costs, Decimal(0)),
            model_used=model_used,
            outcome=outcome,
            promoted_call=promoted_call,
            step_outputs=step_outputs,
        )

    # ------------------------------------------------------------ internals

    async def _load_history(self, turn: TurnInput) -> list[ModelMessage] | None:
        """Prior turns of the SAME step resume the conversation; anything else
        (no ref, another step's transcript, a foreign format) starts fresh."""
        if turn.working_transcript_ref is None:
            return None
        prior: dict[str, Any] = await self._store.get_json(turn.working_transcript_ref)
        if prior.get("format") != "pydantic-ai-messages" or prior.get("step_id") != turn.step_id:
            return None
        return list(ModelMessagesTypeAdapter.validate_python(prior["messages"]))

    def _build_tools(
        self,
        effective: list[ToolGrant],
        *,
        data_plane_url: str,
        turn: TurnInput,
        ctx: TurnContext,
        tool_log: list[dict[str, Any]],
        heartbeat: HeartbeatFn | None,
        http_client: httpx.AsyncClient,
        pending_promoted: list[ToolCallRequest],
    ) -> list[Tool[None]]:
        """One Pydantic AI tool per effective grant.

        Inline reads dispatch directly to the environment data plane. Promoted
        tools are converted into a durable request for the workflow to run as
        a separate activity; the next executor re-entry receives its result.
        Only effective tools are registered, so everything else is rejected by
        the framework and recorded.
        """
        counter = {"next_index": 0}

        used_names: set[str] = set()

        def make_tool(grant: ToolGrant) -> Tool[None]:
            model_name = self._model_tool_name(grant.tool_id, used_names)
            used_names.add(model_name)

            async def dispatch(**kwargs: Any) -> Any:
                tool_index = counter["next_index"]
                counter["next_index"] += 1
                if heartbeat is not None:
                    heartbeat({"turn": ctx.turn, "tool_index": tool_index})
                entry: dict[str, Any] = {
                    "index": tool_index,
                    "tool_id": grant.tool_id,
                    "args": kwargs,
                }
                if grant.execution == "promoted":
                    if pending_promoted:
                        entry["status"] = "deferred"
                        entry["reason"] = "one promoted tool is scheduled at a time"
                        tool_log.append(entry)
                        return {"status": "deferred"}
                    call_index = ctx.resume.call_index + 1 if ctx.resume is not None else 0
                    key = promoted_call_key(turn.run_id, turn.step_id, ctx.turn, call_index)
                    args_ref = await self._store.put_json(
                        f"runs/{turn.run_id}/promoted/args-{key}.json", kwargs
                    )
                    call = ToolCallRequest(
                        tool_id=grant.tool_id,
                        activity=(
                            SANDBOX_JOB_ACTIVITY
                            if grant.tool_id.startswith(SANDBOX_TOOL_PREFIX)
                            else PROMOTED_TOOL_ACTIVITY
                        ),
                        args_ref=args_ref,
                        idempotency_key=key,
                    )
                    pending_promoted.append(call)
                    entry["status"] = "promoted"
                    entry["idempotency_key"] = key
                    tool_log.append(entry)
                    return {
                        "status": "scheduled",
                        "tool_id": grant.tool_id,
                        "message": "Execution pauses here and resumes with the durable result.",
                    }

                response = await http_client.post(
                    f"{data_plane_url}/tools/{grant.tool_id}",
                    json={"args": kwargs, "run_id": turn.run_id, "step_id": turn.step_id},
                    headers=(
                        {"X-Convoy-Internal": self._environments_internal_token}
                        if self._environments_internal_token
                        else None
                    ),
                )
                if response.status_code != 200:
                    entry["status"] = "error"
                    entry["error"] = f"data plane returned HTTP {response.status_code}"
                    tool_log.append(entry)
                    return {"error": entry["error"]}
                payload = response.json()
                entry["status"] = "ok"
                entry["result"] = payload.get("result")
                tool_log.append(entry)
                return payload.get("result")

            return Tool.from_schema(
                dispatch,
                name=model_name,
                description=self._tool_description(grant),
                json_schema=self._tool_schema(grant),
            )

        return [make_tool(grant) for grant in effective]

    @staticmethod
    def _tool_description(grant: ToolGrant) -> str:
        if grant.tool_id == "sandbox_exec":
            return (
                "Execute one command in the run's durable isolated workspace. "
                "The command argument is required and must be an argv array, for example "
                '["sh", "-c", "mkdir -p outputs; echo ok > outputs/result.txt"]. '
                f"Scope: {grant.scope.resource}."
            )
        return f"Convoy tool {grant.tool_id} (scope: {grant.scope.resource})"

    @staticmethod
    def _tool_schema(grant: ToolGrant) -> dict[str, Any]:
        """Expose the runtime-owned sandbox contract to real models.

        Connector schemas will ultimately come from the environment registry;
        until then they remain open objects.  The sandbox executor is owned by
        the runtime, so accepting an empty object there would silently run the
        activity's no-op fallback and make an apparent tool call do no work.
        """
        if grant.tool_id == "sandbox_exec":
            return {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "description": "Command argv to execute in the workspace.",
                    },
                    "env": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                        "description": "Optional non-secret environment variables.",
                    },
                },
                "required": ["command"],
                "additionalProperties": False,
            }
        return {"type": "object", "additionalProperties": True}

    @staticmethod
    def _model_tool_name(tool_id: str, used: set[str]) -> str:
        """Map canonical connector ids onto provider-safe function names."""
        candidate = re.sub(r"[^A-Za-z0-9_-]", "__", tool_id) or "convoy_tool"
        if len(candidate) > 64:
            suffix = hashlib.sha256(tool_id.encode()).hexdigest()[:8]
            candidate = candidate[:55] + "_" + suffix
        if candidate in used:
            suffix = hashlib.sha256(tool_id.encode()).hexdigest()[:8]
            candidate = candidate[:55] + "_" + suffix
        return candidate

    async def _run_with_fallback(
        self,
        *,
        chain: list[str],
        provider: OpenAIProvider,
        tools: list[Tool[None]],
        header: dict[str, Any],
        turn: TurnInput,
        history: list[ModelMessage] | None,
        resume_result: dict[str, Any] | None,
    ) -> tuple[Any, str]:
        """Try each approved model in chain order; transport-level failures
        (connect errors, 5xx, 429) fall through to the next model."""
        instructions = _SYSTEM_INSTRUCTIONS + render_pinned_header(header)
        prompt = self._user_prompt(
            turn,
            header,
            resuming=history is not None,
            resume_result=resume_result,
        )
        last_error: Exception | None = None
        for model_name in chain:
            agent: Agent[None, str] = Agent(
                OpenAIChatModel(model_name, provider=provider),
                deps_type=type(None),
                tools=tools,
                instructions=instructions,
            )
            try:
                result = await agent.run(prompt, message_history=history)
                return result, model_name
            except ModelHTTPError as error:
                if error.status_code >= 500 or error.status_code == 429:
                    last_error = error
                    continue
                raise
            except (ModelAPIError, httpx.TransportError) as error:
                last_error = error
                continue
        assert last_error is not None
        raise last_error

    @staticmethod
    def _user_prompt(
        turn: TurnInput,
        header: dict[str, Any],
        *,
        resuming: bool,
        resume_result: dict[str, Any] | None,
    ) -> str:
        steps: list[dict[str, Any]] = list(header.get("plan", []))
        description = next(
            (str(s.get("description", "")) for s in steps if s.get("id") == turn.step_id), ""
        )
        verb = "Continue executing" if resuming else "Execute"
        lines = [f"{verb} step {turn.step_id}: {description}".rstrip(": ")]
        if turn.steers:
            lines.append("Operator steers to take into account:")
            lines.extend(f"- ({steer.mode}) {steer.body}" for steer in turn.steers)
        if resume_result is not None:
            lines.append(
                "The previously scheduled tool has completed. Its durable result is:\n"
                + json.dumps(resume_result, sort_keys=True, default=str)
            )
        return "\n".join(lines)

    @staticmethod
    def _record_rejections(messages: list[ModelMessage], tool_log: list[dict[str, Any]]) -> None:
        """Unknown/unauthorized tool calls surface as retry prompts in the
        message history; mirror them into the tool log so the rejection is an
        explicit transcript record."""
        for message in messages:
            for part in message.parts:
                if isinstance(part, RetryPromptPart) and part.tool_name is not None:
                    content = part.content
                    reason = content if isinstance(content, str) else "invalid tool call"
                    tool_log.append(
                        {
                            "tool_id": part.tool_name,
                            "status": "rejected",
                            "reason": reason,
                        }
                    )
