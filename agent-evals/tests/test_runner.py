"""test_runner.py — integration tests for the suite runner, replay tier, and
reports, driven over the committed corpus (scenarios/sets/renewal-prep-v1.json).
Port of tests/runner.test.ts.

Tests (a)-(d) exercise the full pipeline and therefore need the sibling
components (convoy_evals.sandbox, .executors, .graders, .scoring) to exist —
they are written ahead of those landing and, per the porting contract, carry
NO skip-marks: they fail with ImportError until the siblings land (imports are
inside each test so the failures stay scoped). Test (e) covers the report
renderers, which are self-contained and must pass now.
"""

import os
import tempfile

import pytest

from convoy_evals.reports.rehearsal_report import render_rehearsal_report
from convoy_evals.reports.suite_report import render_suite_report
from convoy_evals.runtime.events import parse_event
from convoy_evals.sandbox.api import WorldBundle
from convoy_evals.schema.scenario import Scenario
from convoy_evals.schema.verdict import SuiteResult, TrialResult

SET_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "scenarios", "sets", "renewal-prep-v1.json")
)
GOLDEN_SCENARIO = "renewal-golden-3"

# Shared across sequential tests: (d) replays (a)'s recorded artifacts.
_GOLDEN = {}


def _fresh_dir(name):
    return tempfile.mkdtemp(prefix="convoy-evals-{0}-".format(name))


# ---------------------------------------------------------------------------
# (a) golden executor -> scenario passes, artifacts written, suite green
# ---------------------------------------------------------------------------


async def test_a_run_suite_golden_passes_writes_artifacts_suite_green():
    from convoy_evals.runner.suite_runner import run_suite

    out_dir = _fresh_dir("golden")
    result = await run_suite(
        SET_PATH,
        {"kind": "scripted", "executor": "golden"},
        scenario_filter=[GOLDEN_SCENARIO],
        judge_mode="cache-only",
        out_dir=out_dir,
    )
    _GOLDEN["out_dir"] = out_dir
    _GOLDEN["result"] = result

    assert len(result.scenarios) == 1, "filter should leave exactly one scenario"
    sv = result.scenarios[0]
    assert sv.scenarioId == GOLDEN_SCENARIO
    assert sv.status == "passed", "expected passed, got {0}: {1}".format(sv.status, sv.passDetail)
    assert sv.invariantViolation is False, "golden run must not violate invariants"
    assert len(sv.trials) >= 1, "at least one trial ran"

    assert os.path.exists(
        os.path.join(out_dir, "events-{0}-t0.jsonl".format(GOLDEN_SCENARIO))
    ), "events JSONL written"
    assert os.path.exists(
        os.path.join(out_dir, "world-{0}-t0.json".format(GOLDEN_SCENARIO))
    ), "world bundle written"
    assert os.path.exists(
        os.path.join(out_dir, "verdicts-{0}-t0.json".format(GOLDEN_SCENARIO))
    ), "verdicts JSON written"
    assert os.path.exists(os.path.join(out_dir, "suite-result.json")), "suite-result.json written"

    assert result.green is True, "suite should be green given only the golden scenario: {0}".format(
        "; ".join(result.greenDetail)
    )


# ---------------------------------------------------------------------------
# (b) violator-no-gate -> invariant violation, suite not green
# ---------------------------------------------------------------------------


async def test_b_violator_no_gate_flags_invariant_violation_not_green():
    from convoy_evals.runner.suite_runner import run_suite

    result = await run_suite(
        SET_PATH,
        {"kind": "scripted", "executor": "violator-no-gate"},
        scenario_filter=[GOLDEN_SCENARIO],
        judge_mode="cache-only",
        out_dir=_fresh_dir("violator-no-gate"),
    )
    sv = result.scenarios[0]
    assert sv.invariantViolation is True, "skipping the gate must violate an invariant"
    assert sv.status != "passed", "violator must not pass"
    assert result.green is False, "suite must not be green with an invariant violation"


# ---------------------------------------------------------------------------
# (c) violator-skips-items on the gauntlet -> decay stats drop, scenario fails
# ---------------------------------------------------------------------------


async def test_c_violator_skips_items_fails_the_gauntlet():
    from convoy_evals.runner.store import load_eval_set, load_scenario
    from convoy_evals.runner.suite_runner import run_suite

    loaded = load_eval_set(SET_PATH)
    gauntlet_id = None
    for sid, path in loaded.scenario_paths.items():
        try:
            if load_scenario(path).kind == "gauntlet":
                gauntlet_id = sid
                break
        except Exception:
            continue  # unparseable scenario — lint's problem, not this test's
    if gauntlet_id is None:
        pytest.skip("no gauntlet scenario present in the corpus yet")

    result = await run_suite(
        SET_PATH,
        {"kind": "scripted", "executor": "violator-skips-items"},
        scenario_filter=[gauntlet_id],
        trials_override=1,
        judge_mode="cache-only",
        out_dir=_fresh_dir("skips-items"),
    )
    sv = result.scenarios[0]
    assert sv.status == "failed", "skipping items must fail the gauntlet: {0}".format(sv.passDetail)
    if sv.meanDecay:
        assert sv.meanDecay.minQ == 0 or sv.meanDecay.auc < 1, (
            "missing items should drag decay stats down (minQ={0}, auc={1})".format(
                sv.meanDecay.minQ, sv.meanDecay.auc
            )
        )


# ---------------------------------------------------------------------------
# (d) replay determinism over (a)'s artifacts
# ---------------------------------------------------------------------------


def _verdict_statuses(result):
    """Flatten every verdict to a sortable scenario|trial|grader|item|status row."""
    rows = []
    for s in result.scenarios:
        for trial in s.trials:
            for v in trial.verdicts:
                rows.append(
                    "{0}|t{1}|{2}|{3}|{4}".format(
                        s.scenarioId, trial.trialIdx, v.graderId, v.scope.itemId or "", v.status
                    )
                )
            for item in trial.items:
                for v in item.verdicts:
                    rows.append(
                        "{0}|t{1}|{2}|{3}|{4}".format(
                            s.scenarioId, trial.trialIdx, v.graderId, item.itemId, v.status
                        )
                    )
    return sorted(rows)


async def test_d_replay_reproduces_identical_verdict_statuses():
    assert _GOLDEN.get("result") is not None, "depends on test (a) having run"
    from convoy_evals.runner.replay import replay_suite

    replayed = await replay_suite(SET_PATH, _GOLDEN["out_dir"])

    assert replayed.subject.label == "replay"
    assert [
        (s.scenarioId, s.status, s.invariantViolation) for s in replayed.scenarios
    ] == [
        (s.scenarioId, s.status, s.invariantViolation) for s in _GOLDEN["result"].scenarios
    ], "scenario statuses must match the original run"
    assert _verdict_statuses(replayed) == _verdict_statuses(
        _GOLDEN["result"]
    ), "per-verdict statuses must be identical"


# ---------------------------------------------------------------------------
# (e) report renderers — self-contained (no sibling components needed)
# ---------------------------------------------------------------------------


def _synthetic_suite_result():
    fail_verdict = {
        "graderId": "end_state.renewal_status",
        "graderVersion": "abc123",
        "class": "quality",
        "scope": {"runId": "r1", "itemId": "packet/POL-2"},
        "status": "fail",
        "score": 0.6,
        "evidence": [
            {"kind": "world", "query": "records.policy.POL-2.renewal_status", "result": ["pending"]}
        ],
    }
    return SuiteResult.model_validate(
        {
            "evalSet": {"name": "synthetic-set", "version": "0"},
            "subject": {"kind": "scripted", "label": "scripted:golden"},
            "startedAt": "2026-08-02T00:00:00.000Z",
            "finishedAt": "2026-08-02T00:01:00.000Z",
            "scenarios": [
                {
                    "scenarioId": "synthetic-gauntlet",
                    "status": "passed",
                    "invariantViolation": False,
                    "trials": [
                        {
                            "trialIdx": 0,
                            "runId": "r1",
                            "status": "completed",
                            "verdicts": [],
                            "items": [
                                {
                                    "itemId": "packet/POL-1",
                                    "ordinal": 1,
                                    "q": 1,
                                    "status": "pass",
                                    "verdicts": [],
                                },
                                {
                                    "itemId": "packet/POL-2",
                                    "ordinal": 2,
                                    "q": 0.6,
                                    "status": "fail",
                                    "verdicts": [fail_verdict],
                                },
                                {
                                    "itemId": "packet/POL-3",
                                    "ordinal": 3,
                                    "q": 0.9,
                                    "status": "pass",
                                    "verdicts": [],
                                },
                            ],
                            "decay": {
                                "slope": -0.05,
                                "auc": 0.83,
                                "firstDecileMean": 1,
                                "lastDecileMean": 0.9,
                                "itemCount": 3,
                                "minQ": 0.6,
                            },
                            "costUsd": 0.42,
                            "simDays": 4.5,
                            "wallMs": 1234,
                        }
                    ],
                    "meanDecay": {
                        "slope": -0.05,
                        "auc": 0.83,
                        "firstDecileMean": 1,
                        "lastDecileMean": 0.9,
                        "itemCount": 3,
                        "minQ": 0.6,
                    },
                    "passDetail": "1/1 trials passed",
                    "totalCostUsd": 0.42,
                }
            ],
            "green": True,
            "greenDetail": ["all scenarios passed"],
            "totalCostUsd": 0.42,
        }
    )


def _synthetic_scenario():
    return Scenario.model_validate(
        {
            "id": "synthetic-rehearsal",
            "title": "Synthetic rehearsal scenario",
            "missionType": "renewal-prep",
            "kind": "single",
            "fixture": {"pack": "none", "packHash": "deadbeef"},
            "t0": "2026-08-01",
            "seed": 7,
            "trigger": {
                "kind": "api",
                "missionSpec": {
                    "missionType": "renewal-prep",
                    "goal": "Prepare the renewal packet for POL-1",
                },
            },
            "counterparties": [
                {
                    "actorId": "broker-jane",
                    "channel": "email",
                    "owns": ["jane@broker.example"],
                    "profile": "cooperative",
                }
            ],
            "approvals": {"mode": "auto_approve", "maxGates": 5},
            "budgets": {"usd": 2, "simTime": "10d", "wallClock": "5m"},
            "answerKeyRef": {"path": "keys/synthetic.key.json", "hash": "deadbeef"},
            "graders": [],
            "provenance": {"kind": "authored"},
        }
    )


def _synthetic_events():
    base = {"missionId": "m1", "wallTs": "2026-08-02T00:00:00.000Z"}
    raw = [
        dict(
            base,
            eventId="e1",
            seq=0,
            ts="2026-08-01T09:00:00.000Z",
            type="mission_started",
            missionType="renewal-prep",
            environmentId="env-1",
            goal="Prepare the renewal packet for POL-1",
            spec={"modelId": "scripted", "promptHashes": {}},
        ),
        dict(
            base,
            eventId="e2",
            seq=1,
            ts="2026-08-01T10:00:00.000Z",
            type="gate_raised",
            gateId="g1",
            kind="action-approval",
            payload={"action": "send_renewal_packet"},
            deadlineAt=None,
            stepTag="send-packet",
        ),
        dict(
            base,
            eventId="e3",
            seq=2,
            ts="2026-08-01T12:30:00.000Z",
            type="gate_resolved",
            gateId="g1",
            resolution="approve",
            resolvedBy="ops@customer.example",
            reason="packet matches the checklist",
        ),
        dict(
            base,
            eventId="e4",
            seq=3,
            ts="2026-08-01T12:35:00.000Z",
            type="artifact_created",
            artifactId="a1",
            hash="f00dfeed00112233",
            tag="renewal-packet",
            mime="application/pdf",
        ),
        dict(
            base,
            eventId="e5",
            seq=4,
            ts="2026-08-02T09:00:00.000Z",
            type="terminal_outcome",
            status="landed",
            judgedBy="agent",
            summary="packet sent and confirmed",
        ),
    ]
    return [parse_event(e) for e in raw]


def _synthetic_world():
    return WorldBundle.from_json(
        {
            "hash": "worldhash1",
            "messages": [
                {
                    "id": "msg1",
                    "threadId": "th1",
                    "from": "jane@broker.example",
                    "to": ["agent@convoy.example"],
                    "subject": "Loss runs attached",
                    "body": "Here you go.",
                    "attachments": [{"name": "loss-runs-2025.pdf", "fileId": "f1"}],
                    "ts": "2026-08-01T11:00:00.000Z",
                    "direction": "inbound",
                }
            ],
            "records": [],
            "files": [],
        }
    )


def test_e_suite_report_svg_and_rehearsal_gate_row():
    suite_html = render_suite_report(_synthetic_suite_result(), item_floor=0.7)
    assert "synthetic-gauntlet" in suite_html, "suite report names the scenario"
    assert "<svg" in suite_html, "suite report embeds inline SVG"
    assert "item floor 0.7" in suite_html, "Q(n) chart draws the item-floor threshold"
    assert "src=" not in suite_html, "self-contained: no external asset references"

    trial = TrialResult.model_validate(
        {
            "trialIdx": 0,
            "runId": "m1",
            "status": "completed",
            "verdicts": [
                {
                    "graderId": "trajectory.gate_before_send",
                    "graderVersion": "v1",
                    "class": "invariant",
                    "scope": {"runId": "m1"},
                    "status": "pass",
                    "score": 1,
                    "evidence": [
                        {"kind": "event", "eventId": "e2", "note": "gate raised before the send"}
                    ],
                }
            ],
            "items": [],
            "decay": None,
            "costUsd": 0.12,
            "simDays": 1.0,
            "wallMs": 500,
        }
    )
    rehearsal_html = render_rehearsal_report(
        _synthetic_scenario(), trial, _synthetic_events(), _synthetic_world()
    )
    assert "Gate raised" in rehearsal_html, "timeline shows the gate row"
    assert "approve" in rehearsal_html, "gate row shows the resolution"
    assert "ops@customer.example" in rehearsal_html, "gate row names the approver"
    assert "broker-jane" in rehearsal_html, "inbound message is labeled with the counterparty actor"
    assert "Sign-off" in rehearsal_html, "sign-off footer block present"
