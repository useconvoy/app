"""Subagent activities: child context assembly and result wrap-up/archival.

Three seams around a child run's edges. `assemble_subagent_header` writes the
child's pinned context (scoped goal, input refs, budget slice) to the
artifact store. `wrap_subagent_result` distills the child's accumulated turn
bookkeeping into the `SubagentResult` the parent will see — the compaction
contract: headline, bounded summary, typed findings, and refs only; the raw
transcript stays behind `transcript_ref`. `archive_subagent_result` is the
parent-side write that claim-checks a landed child's result so the parent can
carry a `StepSummaryRef` instead of the result body.

TODO: model-driven distillation of child transcripts into richer findings
once compaction lands; today the summary is assembled deterministically from
turn bookkeeping.
"""

from typing import Any

from temporalio import activity

from convoy_core import ArtifactRef, Finding, SubagentResult
from convoy_runtime.activities import names
from convoy_runtime.providers.artifact_store import ArtifactStore
from convoy_runtime.workflows.subagent import SubagentBrief, SubagentWrapUp

_HEADLINE_BY_STATUS = {
    "done": "Completed",
    "failed": "Failed",
    "landed_partial": "Landed with partial results",
}
_MAX_HEADLINE_GOAL = 120
_MAX_SUMMARY_CHARS = 1000  # the summary is bounded prose, never a transcript


def build_subagent_result(
    wrapup: SubagentWrapUp,
    transcript_ref: ArtifactRef,
    error_ref: ArtifactRef | None,
) -> SubagentResult:
    """Deterministic distillation of a child's turn bookkeeping. Pure so the
    workflow-test fakes produce byte-identical results to the real activity."""
    brief = wrapup.brief
    goal = brief.goal[:_MAX_HEADLINE_GOAL]
    headline = f"{_HEADLINE_BY_STATUS[wrapup.status]}: {goal}"
    lines = [
        f"Subagent {brief.agent.id} executed step {brief.step_id} "
        f"({wrapup.turns} turn(s), status {wrapup.status}).",
        f"Goal: {goal}",
        f"Outputs: {len(wrapup.outputs)} artifact(s). Cost: {wrapup.cost_usd} USD.",
    ]
    open_questions: list[str] = []
    if wrapup.error:
        lines.append(f"Error: {wrapup.error}")
        open_questions.append(f"Step {brief.step_id} did not finish: {wrapup.error}")
    elif wrapup.status == "landed_partial":
        open_questions.append(f"Step {brief.step_id} landed before its goal was fully met.")
    findings = [
        Finding(key=f"output:{index}", value=ref.key, refs=[ref])
        for index, ref in enumerate(wrapup.outputs, start=1)
    ]
    return SubagentResult(
        subagent_id=brief.agent.id,
        step_id=brief.step_id,
        status=wrapup.status,
        headline=headline,
        summary="\n".join(lines)[:_MAX_SUMMARY_CHARS],
        findings=findings,
        outputs=list(wrapup.outputs),
        open_questions=open_questions,
        cost_usd=wrapup.cost_usd,
        tokens=wrapup.tokens,
        transcript_ref=transcript_ref,
        error_ref=error_ref,
    )


def build_subagent_header(brief: SubagentBrief) -> dict[str, Any]:
    """The child's pinned context: scoped goal, inputs, and its slice."""
    return {
        "run_id": brief.run_id,
        "parent_run_id": brief.parent_run_id,
        "step_id": brief.step_id,
        "goal": brief.goal,
        "inputs": [ref.model_dump(mode="json") for ref in brief.input_refs],
        "budget": {"cap_usd": str(brief.budget_cap_usd)},
        "layer": brief.agent.layer,
    }


class SubagentActivities:
    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    @activity.defn(name=names.ASSEMBLE_SUBAGENT_HEADER)
    async def assemble_subagent_header(self, brief: SubagentBrief) -> ArtifactRef:
        return await self._store.put_json(
            f"runs/{brief.run_id}/pinned.json", build_subagent_header(brief)
        )

    @activity.defn(name=names.WRAP_SUBAGENT_RESULT)
    async def wrap_subagent_result(self, wrapup: SubagentWrapUp) -> SubagentResult:
        """Finalize the child's result. A child that never completed a turn
        still gets a real (empty) transcript artifact so the full-fidelity
        ref is always present; errors are claim-checked next to it."""
        run_id = wrapup.brief.run_id
        transcript_ref = wrapup.transcript_ref
        if transcript_ref is None:
            transcript_ref = await self._store.put_json(
                f"runs/{run_id}/transcripts/{wrapup.brief.step_id}/none.json",
                {"run_id": run_id, "turns": []},
            )
        error_ref = None
        if wrapup.error:
            error_ref = await self._store.put_json(
                f"runs/{run_id}/errors/wrapup.json",
                {"run_id": run_id, "step_id": wrapup.brief.step_id, "error": wrapup.error},
            )
        return build_subagent_result(wrapup, transcript_ref, error_ref)

    @activity.defn(name=names.ARCHIVE_SUBAGENT_RESULT)
    async def archive_subagent_result(
        self, parent_run_id: str, result: SubagentResult
    ) -> ArtifactRef:
        """Archive a landed child's result under the parent run; the parent
        keeps only the returned ref (as a step summary), never the body."""
        return await self._store.put_json(
            f"runs/{parent_run_id}/subagents/{result.step_id}/result.json",
            result.model_dump(mode="json"),
        )
