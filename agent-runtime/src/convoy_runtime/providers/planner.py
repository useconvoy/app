"""Planning: instruction-derived plans and the model planner.

The planner turns an agent's goal + instructions into the initial ``Plan``.
Selection order in ``PlanActivities.create_plan``:

1. Fixture affordances (``fixture_gates`` / ``fixture_fanout`` on the pinned
   payload) keep the deterministic fixture plan — the test lanes' contract.
2. Instructions (the agent's authored step list) build a deterministic plan
   directly: one step per instruction line, executed in order. A line
   starting with ``checkpoint:`` becomes a human approval gate on the step
   that follows it (or the final step when nothing follows) instead of a
   step of its own — this is how an author demands a human pause without
   any model in the loop.
3. With no instructions, a configured model planner asks the model for a
   structured step list through the LiteLLM gateway. Any failure falls back
   to the fixture plan — planning never blocks a run on model availability.
4. Otherwise the 2-step fixture plan stands.

Everything here returns proposals; only the workflow applies plan state, and
the plan engine validates every shape the same way regardless of author.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError

from convoy_core import ArtifactRef, HumanGate, Plan, PlanPatchOp, PlanRevision, PlanStep

CHECKPOINT_PREFIX = "checkpoint:"

# The model planner's output contract: small, strict, and validated. The
# prompt below embeds this shape; ValidationError -> PlannerError -> fixture
# fallback, never a malformed plan reaching the plan engine.
MAX_PLANNED_STEPS = 25


class PlannedStep(BaseModel):
    """One step the planner proposes, before it becomes a PlanStep."""

    description: str = Field(min_length=1, max_length=500)
    # Human approval demanded before this step executes; the prompt is what
    # the approver reads in the checkpoint card.
    checkpoint: str | None = None
    required: bool = True


class ModelPlanOutput(BaseModel):
    steps: list[PlannedStep] = Field(min_length=1, max_length=MAX_PLANNED_STEPS)


class PlannerError(Exception):
    """Model planning failed; the caller falls back to the fixture plan."""


def parse_instructions(instructions: list[str]) -> list[PlannedStep]:
    """Instruction lines -> planned steps.

    A ``checkpoint:`` line gates the next real step (checkpoint text becomes
    the approver's prompt); consecutive checkpoint lines merge; a trailing
    checkpoint gates the final step. Blank lines are ignored.
    """
    steps: list[PlannedStep] = []
    pending_checkpoint: str | None = None
    for raw in instructions:
        line = raw.strip()
        if not line:
            continue
        if line.lower().startswith(CHECKPOINT_PREFIX):
            prompt = line[len(CHECKPOINT_PREFIX) :].strip() or "Approve before continuing."
            pending_checkpoint = (
                prompt if pending_checkpoint is None else f"{pending_checkpoint}; {prompt}"
            )
            continue
        steps.append(PlannedStep(description=line, checkpoint=pending_checkpoint))
        pending_checkpoint = None
    if pending_checkpoint is not None and steps:
        last = steps[-1]
        merged = (
            pending_checkpoint
            if last.checkpoint is None
            else f"{last.checkpoint}; {pending_checkpoint}"
        )
        steps[-1] = last.model_copy(update={"checkpoint": merged})
    return steps


def assemble_plan(
    goal: str,
    success_criteria: list[str],
    planned: list[PlannedStep],
    snapshot_ref: ArtifactRef,
    *,
    author: str = "system",
) -> Plan:
    """Planned steps -> a linear v1 Plan the plan engine will accept."""
    if not planned:
        raise ValueError("a plan needs at least one step")
    steps: list[PlanStep] = []
    for index, spec in enumerate(planned, start=1):
        gate = (
            HumanGate(kind="approval", prompt=spec.checkpoint)
            if spec.checkpoint is not None
            else None
        )
        steps.append(
            PlanStep(
                id=f"step-{index}",
                description=spec.description,
                status="ready" if index == 1 else "pending",
                depends_on=[f"step-{index - 1}"] if index > 1 else [],
                required=spec.required,
                human_gate=gate,
            )
        )
    revision = PlanRevision(
        version=1,
        author="system" if author == "system" else "agent",
        reason="initial",
        ops=[PlanPatchOp(op="add_step", step=step, reason="initial plan") for step in steps],
        snapshot_ref=snapshot_ref,
    )
    return Plan(
        version=1,
        goal=goal,
        success_criteria=success_criteria,
        steps=steps,
        revisions=[revision],
    )


def build_instruction_plan(
    goal: str,
    success_criteria: list[str],
    instructions: list[str],
    snapshot_ref: ArtifactRef,
) -> Plan:
    """Deterministic plan straight from the agent's authored instructions."""
    planned = parse_instructions(instructions)
    if not planned:
        raise ValueError("instructions contained no actionable steps")
    return assemble_plan(goal, success_criteria, planned, snapshot_ref)


_SYSTEM_PROMPT = f"""You are the planning stage of a durable work agent.
Produce a short, executable plan for the goal below. Respond with ONLY a
JSON object of this exact shape (no prose, no markdown fences):
{{"steps": [{{"description": "...", "checkpoint": null, "required": true}}]}}

Rules:
- 2 to {MAX_PLANNED_STEPS} steps, each a single concrete action in imperative voice.
- Steps run strictly in order; do not include numbering in descriptions.
- Set "checkpoint" to a short question for a human approver on any step
  whose effects reach outside the organization (sending, posting, filing);
  otherwise null.
- Set "required" false only for steps the goal can succeed without.
"""


class ModelPlanner:
    """Structured planning through the LiteLLM gateway's OpenAI-compatible
    chat API. The worker's master key authorizes the call — planning happens
    before the run's own budget-capped key exists; a plan costs one small
    completion and is not attributed to the run's budget in v1."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_s: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("model planning requires the LiteLLM base url")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout_s = timeout_s
        self._transport = transport

    async def plan(
        self, goal: str, success_criteria: list[str], tool_ids: list[str]
    ) -> list[PlannedStep]:
        user_lines = [f"Goal: {goal}"]
        if success_criteria:
            user_lines.append("Success criteria: " + "; ".join(success_criteria))
        if tool_ids:
            user_lines.append("Available tools: " + ", ".join(sorted(tool_ids)))
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": "\n".join(user_lines)},
            ],
            "temperature": 0,
        }
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=self._timeout_s
            ) as client:
                response = await client.post(
                    f"{self._base_url}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as err:
            raise PlannerError(f"model planning request failed: {err}") from err
        try:
            content = body["choices"][0]["message"]["content"]
            parsed = ModelPlanOutput.model_validate(json.loads(_strip_fences(content)))
        except (KeyError, IndexError, TypeError, ValueError, ValidationError) as err:
            raise PlannerError(f"model plan was not parseable: {err}") from err
        return parsed.steps


def _strip_fences(content: str) -> str:
    """Tolerate a model wrapping its JSON in markdown fences despite the
    prompt; anything further from the contract fails validation honestly."""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()
