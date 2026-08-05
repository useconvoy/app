"""Human-in-the-loop flows against the compose stack: plan approval (block,
wrong-version conflict, rejection + pause), human gates over the respond
endpoint, steer note/redirect flows through the scripted executor's
assessment contract, and actor propagation asserted endpoint by endpoint."""

import time
import uuid
from typing import Any

import httpx
import pytest
from _support.e2e import auth_headers, collect_sse, wait_status

pytestmark = pytest.mark.e2e

APPROVAL_POLICY: dict[str, Any] = {"require_plan_approval": True}


def _unique_run_id(label: str) -> str:
    return f"run-e2e-{label}-{uuid.uuid4().hex[:8]}"


def _create(
    api: httpx.Client,
    label: str,
    *,
    policy: dict[str, Any] | None = None,
    fixture_gates: dict[str, Any] | None = None,
    actor: str = "e2e@convoy.test",
    goal: str = "human in the loop",
) -> str:
    body: dict[str, Any] = {"goal": goal, "run_id": _unique_run_id(label)}
    if policy is not None:
        body["policy"] = policy
    if fixture_gates is not None:
        body["fixture_gates"] = fixture_gates
    created = api.post("/runs", json=body, headers=auth_headers(actor=actor))
    assert created.status_code == 202
    return created.json()["run_id"]


def _approve(
    api: httpx.Client, run_id: str, version: int, *, actor: str = "e2e@convoy.test"
) -> httpx.Response:
    return api.post(
        f"/runs/{run_id}/plan/approve",
        json={"plan_version": version, "approve": True},
        headers=auth_headers(actor=actor),
    )


def _wait_plan_version(
    api: httpx.Client, run_id: str, version: int, timeout: float = 90.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = api.get(f"/runs/{run_id}", headers=auth_headers())
        assert response.status_code == 200
        last = response.json()
        plan: dict[str, Any] = last.get("plan") or {}
        if plan.get("version") == version:
            return last
        time.sleep(0.2)
    raise TimeoutError(f"run {run_id} never reached plan version {version}: {last.get('plan')}")


# ------------------------------------------------------------- plan approval


def test_plan_approval_blocks_until_approved(api: httpx.Client) -> None:
    run_id = _create(api, "approve", policy=APPROVAL_POLICY)
    assert wait_status(api, run_id, {"awaiting_approval"}) == "awaiting_approval"

    run = api.get(f"/runs/{run_id}", headers=auth_headers()).json()
    assert run["plan"]["version"] == 1
    # No step may run while approval is pending.
    assert all(step["status"] in {"pending", "ready"} for step in run["steps"])

    # A decision for any other version is a clean conflict.
    stale = _approve(api, run_id, 99)
    assert stale.status_code == 409

    approved = _approve(api, run_id, 1, actor="approver@convoy.test")
    assert approved.status_code == 202
    wait_status(api, run_id, {"completed"})

    events = collect_sse(api, run_id, terminal={"run_completed"})
    types = [e["type"] for e in events]
    assert "revision_approved" in types
    assert types.index("revision_approved") < types.index("step_started")
    approval = next(e for e in events if e["type"] == "revision_approved")
    assert approval["actor"] == "approver@convoy.test"
    plan_created = next(e for e in events if e["type"] == "plan_created")
    assert plan_created["payload"]["run_status"] == "awaiting_approval"


def test_plan_rejection_records_reason_and_pauses(api: httpx.Client) -> None:
    run_id = _create(api, "reject", policy=APPROVAL_POLICY)
    wait_status(api, run_id, {"awaiting_approval"})

    # Rejections must carry a reason.
    missing = api.post(
        f"/runs/{run_id}/plan/approve",
        json={"plan_version": 1, "approve": False},
        headers=auth_headers(),
    )
    assert missing.status_code == 422

    rejected = api.post(
        f"/runs/{run_id}/plan/approve",
        json={"plan_version": 1, "approve": False, "reason": "rescope to the EU market"},
        headers=auth_headers(actor="rejector@convoy.test"),
    )
    assert rejected.status_code == 202
    wait_status(api, run_id, {"paused"})

    events = collect_sse(api, run_id, terminal={"paused"})
    rejection = next(e for e in events if e["type"] == "revision_rejected")
    assert rejection["actor"] == "rejector@convoy.test"
    assert rejection["payload"]["reason"] == "rescope to the EU market"
    assert rejection["payload"]["plan_version"] == 1

    # Resume alone re-enters the approval wait; approval then releases it.
    assert api.post(f"/runs/{run_id}/resume", headers=auth_headers()).status_code == 202
    wait_status(api, run_id, {"awaiting_approval"})
    assert _approve(api, run_id, 1).status_code == 202
    wait_status(api, run_id, {"completed"})


# --------------------------------------------------------------- human gates


def test_gate_blocks_step_and_respond_unblocks_it(api: httpx.Client) -> None:
    run_id = _create(
        api,
        "gate",
        fixture_gates={"step-2": {"kind": "approval", "prompt": "Ship the summary?"}},
    )
    assert wait_status(api, run_id, {"blocked_on_human"}) == "blocked_on_human"

    run = api.get(f"/runs/{run_id}", headers=auth_headers()).json()
    blocked = {s["step_id"]: s["status"] for s in run["steps"]}
    assert blocked["step-2"] == "blocked_on_human"

    # Responding to a step that is not gated is a clean conflict; an unknown
    # step is not found.
    wrong = api.post(
        f"/runs/{run_id}/steps/step-1/respond",
        json={"response": "nope"},
        headers=auth_headers(),
    )
    assert wrong.status_code == 409
    missing = api.post(
        f"/runs/{run_id}/steps/step-99/respond",
        json={"response": "nope"},
        headers=auth_headers(),
    )
    assert missing.status_code == 404

    answered = api.post(
        f"/runs/{run_id}/steps/step-2/respond",
        json={"response": "yes, ship it"},
        headers=auth_headers(actor="responder@convoy.test"),
    )
    assert answered.status_code == 202
    wait_status(api, run_id, {"completed"})

    events = collect_sse(api, run_id, terminal={"run_completed"})
    opened = next(e for e in events if e["type"] == "gate_opened")
    assert opened["payload"]["step_id"] == "step-2"
    assert opened["payload"]["prompt"] == "Ship the summary?"
    assert opened["actor"] == "system"
    gate_answered = next(e for e in events if e["type"] == "gate_answered")
    assert gate_answered["payload"]["response"] == "yes, ship it"
    assert gate_answered["actor"] == "responder@convoy.test"
    assert gate_answered["actor_type"] == "human"


# --------------------------------------------------------------------- steer


def test_steer_note_and_covered_redirect_are_recorded(api: httpx.Client) -> None:
    run_id = _create(api, "steer-note", policy=APPROVAL_POLICY)
    wait_status(api, run_id, {"awaiting_approval"})

    note = api.post(
        f"/runs/{run_id}/steer",
        json={"mode": "note", "body": "check the runway figure"},
        headers=auth_headers(actor="noter@convoy.test"),
    )
    assert note.status_code == 202
    redirect = api.post(
        f"/runs/{run_id}/steer",
        json={"mode": "redirect", "body": "[covered] focus on enterprise accounts"},
        headers=auth_headers(actor="redirector@convoy.test"),
    )
    assert redirect.status_code == 202
    redirect_id = redirect.json()["steer_id"]

    assert _approve(api, run_id, 1).status_code == 202
    wait_status(api, run_id, {"completed"})

    events = collect_sse(api, run_id, terminal={"run_completed"})
    steers = [e for e in events if e["type"] == "steer_received"]
    assert [e["actor"] for e in steers] == ["noter@convoy.test", "redirector@convoy.test"]

    # The covered redirect leaves an explicit assessment record, not a fork.
    assessment = next(
        e
        for e in events
        if e["type"] == "revision_rejected" and e["payload"].get("kind") == "steer_assessment"
    )
    assert assessment["payload"]["reason"] == "plan_already_covers"
    assert redirect_id in assessment["payload"]["steer_ids"]
    assert not any(e["type"] == "revision_applied" for e in events)
    # The plan stayed at two steps.
    run = api.get(f"/runs/{run_id}", headers=auth_headers()).json()
    assert len(run["plan"]["steps"]) == 2


def test_steer_redirect_produces_approved_revision(api: httpx.Client) -> None:
    run_id = _create(
        api,
        "steer-redirect",
        policy={"require_plan_approval": True, "approval_scope": "major_revisions"},
    )
    wait_status(api, run_id, {"awaiting_approval"})

    redirect = api.post(
        f"/runs/{run_id}/steer",
        json={"mode": "redirect", "body": "also produce a competitive analysis"},
        headers=auth_headers(actor="redirector@convoy.test"),
    )
    assert redirect.status_code == 202
    redirect_id = redirect.json()["steer_id"]

    assert _approve(api, run_id, 1).status_code == 202

    # The assessment turn proposes a revision, which re-enters approval as
    # v2; waiting on the version distinguishes this from the initial wait.
    run = _wait_plan_version(api, run_id, 2)
    assert run["status"] == "awaiting_approval"
    assert _approve(api, run_id, 2, actor="approver2@convoy.test").status_code == 202
    wait_status(api, run_id, {"completed"})

    run = api.get(f"/runs/{run_id}", headers=auth_headers()).json()
    assert len(run["plan"]["steps"]) == 3
    revisions = run["plan"]["revisions"]
    assert len(revisions) == 2
    assert revisions[1]["reason"] == "steer"
    assert revisions[1]["author"] == "agent"

    events = collect_sse(api, run_id, terminal={"run_completed"})
    applied = next(e for e in events if e["type"] == "revision_applied")
    assert applied["payload"]["steer_ids"] == [redirect_id]
    assert applied["payload"]["requires_approval"] is True
    approvals = [e for e in events if e["type"] == "revision_approved"]
    assert [e["payload"]["plan_version"] for e in approvals] == [1, 2]
    assert approvals[1]["actor"] == "approver2@convoy.test"
    # All three steps (including the added one) completed.
    done = [e for e in events if e["type"] == "step_done"]
    assert len(done) == 3


# --------------------------------------------------------- actor propagation


def test_actor_propagation_per_endpoint(api: httpx.Client) -> None:
    """Every external endpoint's action lands in RunEvents with the verified
    actor, asserted endpoint by endpoint on one choreographed run (plus a
    dedicated run for land)."""
    run_id = _create(
        api,
        "actors",
        policy=APPROVAL_POLICY,
        fixture_gates={"step-2": {"kind": "approval", "prompt": "Proceed?"}},
        actor="actor-create@convoy.test",
    )
    wait_status(api, run_id, {"awaiting_approval"})

    steer = api.post(
        f"/runs/{run_id}/steer",
        json={"mode": "note", "body": "remember the deadline"},
        headers=auth_headers(actor="actor-steer@convoy.test"),
    )
    assert steer.status_code == 202
    assert (
        api.post(
            f"/runs/{run_id}/pause", headers=auth_headers(actor="actor-pause@convoy.test")
        ).status_code
        == 202
    )
    wait_status(api, run_id, {"paused"})
    assert (
        api.post(
            f"/runs/{run_id}/resume", headers=auth_headers(actor="actor-resume@convoy.test")
        ).status_code
        == 202
    )
    wait_status(api, run_id, {"awaiting_approval"})
    assert _approve(api, run_id, 1, actor="actor-approve@convoy.test").status_code == 202
    wait_status(api, run_id, {"blocked_on_human"})
    assert (
        api.post(
            f"/runs/{run_id}/steps/step-2/respond",
            json={"response": "go ahead"},
            headers=auth_headers(actor="actor-respond@convoy.test"),
        ).status_code
        == 202
    )
    wait_status(api, run_id, {"completed"})

    events = collect_sse(api, run_id, terminal={"run_completed"})

    def actor_of(event_type: str) -> str:
        return next(e for e in events if e["type"] == event_type)["actor"]

    assert actor_of("run_started") == "actor-create@convoy.test"  # POST /runs
    assert actor_of("steer_received") == "actor-steer@convoy.test"  # POST .../steer
    assert actor_of("paused") == "actor-pause@convoy.test"  # POST .../pause
    assert actor_of("resumed") == "actor-resume@convoy.test"  # POST .../resume
    assert actor_of("revision_approved") == "actor-approve@convoy.test"  # POST .../plan/approve
    assert actor_of("gate_answered") == "actor-respond@convoy.test"  # POST .../steps/{sid}/respond
    for event in events:
        assert event["actor"], f"event {event['type']} has no actor"

    # Land gets its own run so the wrap-up is deterministic.
    land_run_id = _create(api, "actors-land", policy=APPROVAL_POLICY)
    wait_status(api, land_run_id, {"awaiting_approval"})
    assert (
        api.post(
            f"/runs/{land_run_id}/land", headers=auth_headers(actor="actor-land@convoy.test")
        ).status_code
        == 202
    )
    wait_status(api, land_run_id, {"completed"})
    land_events = collect_sse(api, land_run_id, terminal={"run_completed"})
    landing = next(e for e in land_events if e["type"] == "landing_started")
    assert landing["actor"] == "actor-land@convoy.test"  # POST .../land
