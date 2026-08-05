"""Live human-in-the-loop lane: the human control surface (plan approval,
pause/resume, steer notes and redirects, human gates, early land, budget
exhaustion) exercised against a REAL model through the LiteLLM proxy.

Opt-in exactly like the live-smoke lane — `make live-smoke` runs it, and
without a provider key every test skips cleanly. Each test creates one run
with a tight budget and a goal crafted for one-short-sentence replies, then
proves the human action actually changed what reached the real model by
reading the claim-checked transcripts back from the artifact store: steer
notes and gate answers must appear in the prompt context AND in the model's
real output, not merely in the event log.

The real executor cannot propose plan revisions, so a redirect steer lands on
the audited assessment path (`revision_rejected` with kind
`steer_assessment`) instead of forking the plan; the redirect test pins that
behavior down.
"""

import json
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import boto3
import httpx
import pytest
from _support.e2e import auth_headers, collect_sse, live_smoke_model, wait_status
from botocore.config import Config as BotoConfig

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        live_smoke_model() is None,
        reason=(
            "live HITL tests need a real model provider key: set ANTHROPIC_API_KEY "
            "(default model claude-sonnet-5) or OPENAI_API_KEY, then run `make live-smoke`"
        ),
    ),
]

# The compose stack's MinIO as reachable from the host. Transcripts and step
# outputs are read back directly so the tests can assert on what the real
# model was shown and what it actually said.
ARTIFACT_BUCKET = "convoy-artifacts"
ARTIFACT_ENDPOINT = "http://localhost:9000"
ARTIFACT_ACCESS_KEY = "convoy"
ARTIFACT_SECRET_KEY = "convoy-secret-key"

APPROVAL_POLICY: dict[str, Any] = {"require_plan_approval": True}

# Keep every live run cheap: one-short-sentence replies, tight dollar cap.
LIVE_BUDGET_USD = "1.00"


def _unique_run_id(label: str) -> str:
    return f"run-live-hitl-{label}-{uuid.uuid4().hex[:8]}"


def _create(
    api: httpx.Client,
    label: str,
    *,
    model: str,
    goal: str,
    policy: dict[str, Any] | None = None,
    fixture_gates: dict[str, Any] | None = None,
    budget_usd: str = LIVE_BUDGET_USD,
    actor: str = "e2e@convoy.test",
) -> str:
    body: dict[str, Any] = {
        "goal": goal,
        "run_id": _unique_run_id(label),
        "model": model,
        "budget_usd": budget_usd,
    }
    if policy is not None:
        body["policy"] = policy
    if fixture_gates is not None:
        body["fixture_gates"] = fixture_gates
    created = api.post("/runs", json=body, headers=auth_headers(actor=actor))
    assert created.status_code == 202, created.text
    return created.json()["run_id"]


def _approve(
    api: httpx.Client, run_id: str, version: int, *, actor: str = "e2e@convoy.test"
) -> httpx.Response:
    return api.post(
        f"/runs/{run_id}/plan/approve",
        json={"plan_version": version, "approve": True},
        headers=auth_headers(actor=actor),
    )


def _run_view(api: httpx.Client, run_id: str) -> dict[str, Any]:
    response = api.get(f"/runs/{run_id}", headers=auth_headers())
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------- artifact readback


def _artifact_client() -> "S3Client":
    return boto3.client(  # pyright: ignore[reportUnknownMemberType]
        "s3",
        endpoint_url=ARTIFACT_ENDPOINT,
        region_name="us-east-1",
        aws_access_key_id=ARTIFACT_ACCESS_KEY,
        aws_secret_access_key=ARTIFACT_SECRET_KEY,
        config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def _step_transcripts(run_id: str, step_id: str) -> list[dict[str, Any]]:
    """Every turn transcript the executor archived for one step, in turn
    order — a step may take several turns on a real model."""
    client = _artifact_client()
    prefix = f"runs/{run_id}/transcripts/{step_id}/"
    listing = client.list_objects_v2(Bucket=ARTIFACT_BUCKET, Prefix=prefix)
    documents: list[dict[str, Any]] = []
    for entry in listing.get("Contents", []):
        key = entry.get("Key")
        if key is None:
            continue
        body = client.get_object(Bucket=ARTIFACT_BUCKET, Key=key)["Body"].read()
        documents.append(json.loads(body))
    documents.sort(key=lambda doc: doc.get("turn", 0))
    return documents


def _step_reply(run_id: str, step_id: str) -> str:
    """The step's final model output, as archived when the step completed."""
    client = _artifact_client()
    key = f"runs/{run_id}/outputs/{step_id}.json"
    body = client.get_object(Bucket=ARTIFACT_BUCKET, Key=key)["Body"].read()
    output: dict[str, Any] = json.loads(body)
    return str(output["response"])


def _prompt_context(documents: list[dict[str, Any]]) -> str:
    """Every string in the message history across a step's turns, joined —
    the haystack for 'did this text reach the real prompt' assertions.
    Comparing raw strings keeps quotes in the needle from tripping over JSON
    escaping."""
    strings: list[str] = []

    def _walk(node: Any) -> None:
        if isinstance(node, str):
            strings.append(node)
        elif isinstance(node, dict):
            for value in node.values():  # pyright: ignore[reportUnknownVariableType]
                _walk(value)
        elif isinstance(node, list):
            for value in node:  # pyright: ignore[reportUnknownVariableType]
                _walk(value)

    _walk([doc.get("messages", []) for doc in documents])
    return "\n".join(strings)


# ------------------------------------------------------------- plan approval


def test_plan_approval_blocks_the_first_real_turn(api_live: httpx.Client, live_stack: str) -> None:
    run_id = _create(
        api_live,
        "approve",
        model=live_stack,
        policy=APPROVAL_POLICY,
        goal=(
            "Live approval test: no investigation is needed. For every step, "
            "reply with one short sentence confirming the step is complete."
        ),
    )
    assert wait_status(api_live, run_id, {"awaiting_approval"}) == "awaiting_approval"

    # No step may touch the model while approval is pending.
    run = _run_view(api_live, run_id)
    assert run["plan"]["version"] == 1
    assert all(step["status"] in {"pending", "ready"} for step in run["steps"])

    # A decision pinned to any other plan version is a clean conflict.
    stale = _approve(api_live, run_id, 99)
    assert stale.status_code == 409, stale.text

    approved = _approve(api_live, run_id, 1, actor="approver@convoy.test")
    assert approved.status_code == 202, approved.text
    wait_status(api_live, run_id, {"completed"}, timeout=180)

    events = collect_sse(api_live, run_id, terminal={"run_completed"})
    types = [e["type"] for e in events]
    assert "step_started" not in types[: types.index("revision_approved")]
    approval = next(e for e in events if e["type"] == "revision_approved")
    assert approval["actor"] == "approver@convoy.test"

    step_done = [e for e in events if e["type"] == "step_done"]
    assert len(step_done) == 2
    for event in step_done:
        assert event["payload"]["model_used"] == live_stack
        assert event["payload"]["model_fallback"] is False

    run = _run_view(api_live, run_id)
    assert run["status"] == "completed"
    assert Decimal(run["budget"]["spent_usd"]) > 0


# -------------------------------------------------------------- pause/resume


def test_pause_resume_completes_without_step_damage(
    api_live: httpx.Client, live_stack: str
) -> None:
    run_id = _create(
        api_live,
        "pause",
        model=live_stack,
        policy=APPROVAL_POLICY,
        goal=(
            "Live pause test: no investigation is needed. For every step, "
            "reply with one short sentence confirming the step is complete."
        ),
    )
    wait_status(api_live, run_id, {"awaiting_approval"})

    # Pause during the approval wait is deterministic: no turn is in flight.
    paused = api_live.post(
        f"/runs/{run_id}/pause", headers=auth_headers(actor="pauser@convoy.test")
    )
    assert paused.status_code == 202, paused.text
    wait_status(api_live, run_id, {"paused"})

    resumed = api_live.post(
        f"/runs/{run_id}/resume", headers=auth_headers(actor="resumer@convoy.test")
    )
    assert resumed.status_code == 202, resumed.text
    wait_status(api_live, run_id, {"awaiting_approval"})

    assert _approve(api_live, run_id, 1).status_code == 202
    wait_status(api_live, run_id, {"completed"}, timeout=180)

    events = collect_sse(api_live, run_id, terminal={"run_completed"})
    types = [e["type"] for e in events]
    paused_event = next(e for e in events if e["type"] == "paused")
    assert paused_event["actor"] == "pauser@convoy.test"
    assert paused_event["actor_type"] == "human"
    resumed_event = next(e for e in events if e["type"] == "resumed")
    assert resumed_event["actor"] == "resumer@convoy.test"

    # The pause damaged nothing: no step failed and every step landed done.
    assert "step_failed" not in types
    assert "run_failed" not in types
    run = _run_view(api_live, run_id)
    assert run["status"] == "completed"
    assert all(step["status"] == "done" for step in run["steps"])


# ---------------------------------------------------------------- steer note


def test_steer_note_reaches_the_real_prompt_and_output(
    api_live: httpx.Client, live_stack: str
) -> None:
    note_body = 'Operator instruction: include the exact word "pineapple" in your next reply.'
    run_id = _create(
        api_live,
        "note",
        model=live_stack,
        policy=APPROVAL_POLICY,
        goal=(
            "Live steer test: no investigation is needed. For every step, reply "
            "with one short sentence, and follow any operator instruction exactly."
        ),
    )
    wait_status(api_live, run_id, {"awaiting_approval"})

    # Queued before approval, the note is guaranteed to ride into the first
    # real turn's context.
    note = api_live.post(
        f"/runs/{run_id}/steer",
        json={"mode": "note", "body": note_body},
        headers=auth_headers(actor="noter@convoy.test"),
    )
    assert note.status_code == 202, note.text
    steer_id = note.json()["steer_id"]

    assert _approve(api_live, run_id, 1).status_code == 202
    wait_status(api_live, run_id, {"completed"}, timeout=180)

    events = collect_sse(api_live, run_id, terminal={"run_completed"})
    received = next(e for e in events if e["type"] == "steer_received")
    assert received["actor"] == "noter@convoy.test"

    # The note reached the real prompt: the archived transcript records the
    # steer as drained and carries its text in the message history.
    transcripts = _step_transcripts(run_id, "step-1")
    assert transcripts, "no step-1 transcript archived"
    drained = {sid for doc in transcripts for sid in doc.get("steers_drained", [])}
    assert steer_id in drained
    assert note_body in _prompt_context(transcripts)

    # ...and it changed the real output.
    reply = _step_reply(run_id, "step-1")
    assert "pineapple" in reply.lower(), f"real model reply ignored the note: {reply!r}"


# ------------------------------------------------------------ steer redirect


def test_redirect_steer_lands_on_the_assessment_path(
    api_live: httpx.Client, live_stack: str
) -> None:
    """The real executor has no way to propose plan revisions, so a redirect
    must resolve through the audited assessment record — an explicit
    `revision_rejected`/`steer_assessment` event with a transcript ref — and
    the run must continue on the unchanged plan."""
    run_id = _create(
        api_live,
        "redirect",
        model=live_stack,
        policy=APPROVAL_POLICY,
        goal=(
            "Live redirect test: no investigation is needed. For every step, "
            "reply with one short sentence confirming the step is complete."
        ),
    )
    wait_status(api_live, run_id, {"awaiting_approval"})

    redirect = api_live.post(
        f"/runs/{run_id}/steer",
        json={
            "mode": "redirect",
            "body": "Change of approach: phrase the summary as a haiku instead of a sentence.",
        },
        headers=auth_headers(actor="redirector@convoy.test"),
    )
    assert redirect.status_code == 202, redirect.text
    steer_id = redirect.json()["steer_id"]

    assert _approve(api_live, run_id, 1).status_code == 202
    wait_status(api_live, run_id, {"completed"}, timeout=180)

    events = collect_sse(api_live, run_id, terminal={"run_completed"})
    assessment = next(
        e
        for e in events
        if e["type"] == "revision_rejected" and e["payload"].get("kind") == "steer_assessment"
    )
    assert assessment["actor_type"] == "agent"
    assert assessment["payload"]["reason"] == "plan_already_covers"
    assert steer_id in assessment["payload"]["steer_ids"]
    assert assessment["payload"]["plan_version"] == 1
    assert assessment["payload"]["transcript_ref"]["key"].startswith(f"runs/{run_id}/transcripts/")

    # No fork happened: the plan stayed at version 1 with its two steps, and
    # the run completed anyway.
    assert not any(e["type"] == "revision_applied" for e in events)
    run = _run_view(api_live, run_id)
    assert run["status"] == "completed"
    assert run["plan"]["version"] == 1
    assert len(run["plan"]["steps"]) == 2


# ---------------------------------------------------------------- human gate


def test_gate_answer_feeds_the_real_models_context(api_live: httpx.Client, live_stack: str) -> None:
    run_id = _create(
        api_live,
        "gate",
        model=live_stack,
        fixture_gates={
            "step-2": {"kind": "input", "prompt": "Which fruit should the reply mention?"}
        },
        goal=(
            "Live gate test: no investigation is needed. Reply with one short "
            "sentence per step. A human will name a fruit while the run is in "
            "flight; the summary step's sentence must name that fruit."
        ),
    )
    assert wait_status(api_live, run_id, {"blocked_on_human"}, timeout=120) == "blocked_on_human"

    run = _run_view(api_live, run_id)
    statuses = {step["step_id"]: step["status"] for step in run["steps"]}
    assert statuses["step-2"] == "blocked_on_human"

    answered = api_live.post(
        f"/runs/{run_id}/steps/step-2/respond",
        json={"response": "dragonfruit"},
        headers=auth_headers(actor="responder@convoy.test"),
    )
    assert answered.status_code == 202, answered.text
    wait_status(api_live, run_id, {"completed"}, timeout=180)

    events = collect_sse(api_live, run_id, terminal={"run_completed"})
    opened = next(e for e in events if e["type"] == "gate_opened")
    assert opened["payload"]["step_id"] == "step-2"
    assert opened["payload"]["kind"] == "input"
    assert opened["payload"]["prompt"] == "Which fruit should the reply mention?"
    gate_answered = next(e for e in events if e["type"] == "gate_answered")
    assert gate_answered["payload"]["response"] == "dragonfruit"
    assert gate_answered["actor"] == "responder@convoy.test"
    assert gate_answered["actor_type"] == "human"

    # The answer fed the real model: it appears in step-2's prompt context
    # (as the gate-answer note) and in the model's real reply.
    transcripts = _step_transcripts(run_id, "step-2")
    assert transcripts, "no step-2 transcript archived"
    drained = {sid for doc in transcripts for sid in doc.get("steers_drained", [])}
    assert any(sid.startswith("gate-answer-step-2") for sid in drained)
    assert "dragonfruit" in _prompt_context(transcripts)

    reply = _step_reply(run_id, "step-2")
    assert "dragonfruit" in reply.lower(), f"real model reply ignored the gate answer: {reply!r}"


# ---------------------------------------------------------------- early land


def test_early_land_while_gated_wraps_up_partial(api_live: httpx.Client, live_stack: str) -> None:
    run_id = _create(
        api_live,
        "land",
        model=live_stack,
        fixture_gates={"step-2": {"kind": "approval", "prompt": "Ship the summary?"}},
        goal=(
            "Live land test: no investigation is needed. For every step, reply "
            "with one short sentence confirming the step is complete."
        ),
    )
    wait_status(api_live, run_id, {"blocked_on_human"}, timeout=120)

    landed = api_live.post(f"/runs/{run_id}/land", headers=auth_headers(actor="lander@convoy.test"))
    assert landed.status_code == 202, landed.text
    wait_status(api_live, run_id, {"completed"})

    events = collect_sse(api_live, run_id, terminal={"run_completed"})
    types = [e["type"] for e in events]
    assert types[-1] == "run_completed"
    assert types.index("landing_started") < types.index("run_completed")
    landing = next(e for e in events if e["type"] == "landing_started")
    assert landing["actor"] == "lander@convoy.test"
    # Landing abandoned the gated step without damaging it.
    assert "step_failed" not in types

    run = _run_view(api_live, run_id)
    assert run["status"] == "completed"
    assert run["land_report"]["status"] == "landed_partial"
    assert run["land_report"]["steps_done"] == 1
    statuses = {step["step_id"]: step["status"] for step in run["steps"]}
    assert statuses["step-1"] == "done"
    assert statuses["step-2"] != "done"


# --------------------------------------------------------- budget exhaustion


def test_budget_exhaustion_on_real_spend_pauses_the_run(
    api_live: httpx.Client, live_stack: str
) -> None:
    """A cap so small the first real turn's proxy-priced cost crosses 100%:
    the default policy pauses the run as the system actor, with real
    spent_usd over the cap recorded in the run view."""
    cap = Decimal("0.0001")
    run_id = _create(
        api_live,
        "budget",
        model=live_stack,
        budget_usd=str(cap),
        goal=(
            "Live budget test: no investigation is needed. For every step, "
            "reply with one short sentence confirming the step is complete."
        ),
    )
    wait_status(api_live, run_id, {"paused"}, timeout=120)

    events = collect_sse(api_live, run_id, terminal={"paused"})
    types = [e["type"] for e in events]
    assert types.index("budget_exhausted") < types.index("paused")
    exhausted = next(e for e in events if e["type"] == "budget_exhausted")
    assert exhausted["payload"]["action"] == "pause"
    assert Decimal(exhausted["payload"]["budget"]["spent_usd"]) > cap
    paused = next(e for e in events if e["type"] == "paused")
    assert paused["actor"] == "system"
    assert paused["actor_type"] == "system"

    run = _run_view(api_live, run_id)
    assert Decimal(run["budget"]["cap_usd"]) == cap
    assert Decimal(run["budget"]["spent_usd"]) > cap, "real spend did not exceed the tiny cap"

    # Land the over-budget run to leave nothing running.
    landed = api_live.post(f"/runs/{run_id}/land", headers=auth_headers())
    assert landed.status_code == 202, landed.text
    wait_status(api_live, run_id, {"completed"})
    run = _run_view(api_live, run_id)
    assert run["land_report"]["status"] == "landed_partial"
