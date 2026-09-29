"""Trial authority and CLI wiring; no model, simulator, endpoint or cloud runs."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import sys
from argparse import Namespace

import external_planner as trial
import pytest
from convoy_contracts.execution import canonical_digest
from convoy_contracts.pairing import (
    CATALOG_SHA256,
    FIXED_TASK,
    PAIRED_PROFILE,
    PLANNER_PROTOCOL_SHA256,
    PLANNER_RUNTIME,
    SKILL_ID,
)


@pytest.fixture
def setup(tmp_path):
    directory = tmp_path / "authority"
    trial.prepare(directory)
    manifest = {"schema_version": 2, "profile": PAIRED_PROFILE, "action_manifest": trial.reference_manifest(),
                "planner": {"runtime": PLANNER_RUNTIME, "artifact_sha256": "b" * 64,
                            "protocol_sha256": PLANNER_PROTOCOL_SHA256},
                "task": {"instruction": FIXED_TASK, "skill_id": SKILL_ID}, "catalog_sha256": CATALOG_SHA256,
                "planning": {"timeout_ms": 30000},
                "placement": {"policy": "development-local-cpu", "planner": "development-remote-cpu"}}
    path = tmp_path / "release.json"
    path.write_text(json.dumps(manifest))
    assets = tmp_path / "assets"
    assets.mkdir()
    return Namespace(authority_dir=directory, manifest=path, planner_url="https://planner.example.test",
                     action_assets=assets, output=tmp_path / "run", planner_ca_file=None,
                     direct_report=None, direct_trace=None), manifest


def test_prepare_never_replaces_complete_or_partial_authority(setup):
    args, _ = setup
    files = {path: path.read_bytes() for path in args.authority_dir.rglob("*") if path.is_file()}
    signing, probe = trial.authority(args.authority_dir)
    assert len(probe) == 64 and signing.read_bytes() == files[signing]
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in files)
    assert stat.S_IMODE(args.authority_dir.stat().st_mode) == 0o700
    with pytest.raises(FileExistsError):
        trial.prepare(args.authority_dir)
    assert all(path.read_bytes() == raw for path, raw in files.items())
    (args.authority_dir / "planner-probe.txt").unlink()
    with pytest.raises(FileExistsError):
        trial.prepare(args.authority_dir)
    assert signing.read_bytes() == files[signing]
    with pytest.raises(FileNotFoundError):
        trial.authority(args.authority_dir)


@pytest.mark.parametrize("fault", ["probe-header", "probe-size", "wrong-public", "http"])
def test_invalid_authority_or_transport_stops_before_network_or_local_state(setup, monkeypatch, fault):
    args, _ = setup
    if fault.startswith("probe"):
        raw = b"token\r\nAuthorization: private-marker" if fault == "probe-header" else b"x" * 258
        (args.authority_dir / "planner-probe.txt").write_bytes(raw)
    elif fault == "wrong-public":
        (args.authority_dir / "verification/planner.json").write_bytes(
            (args.authority_dir / "verification/action.json").read_bytes())
    else:
        args.planner_url = "http://127.0.0.1:9101"
    monkeypatch.setattr(trial.pipeline, "probe_external_planner", lambda *_: pytest.fail("invalid inputs reached endpoint"))
    monkeypatch.setattr(trial.pipeline, "run", lambda *_a, **_kw: pytest.fail("invalid inputs launched services"))
    with pytest.raises((ValueError, OSError)):
        trial.run(args)
    assert not args.output.exists()


@pytest.mark.parametrize("baseline_matches", [True, False])
def test_one_mission_wiring_and_real_journal_evidence_require_baseline_match(setup, monkeypatch, baseline_matches):
    args, manifest = setup
    args.direct_report = args.output.parent / "direct.json"
    args.direct_trace = args.output.parent / "direct.jsonl"
    args.direct_report.write_text(json.dumps({"episode": {"steps": 1, "final_success": True}}))
    args.direct_trace.write_text(json.dumps({"applied_action": [0, 0, 0, 0] if baseline_matches else [1, 0, 0, 0]}) + "\n")
    identity = {"robot_id": "robot", "device_id": "device", "mission_id": "mission", "boot_id": "boot",
                "incarnation": "coordinator", "release_digest": canonical_digest(manifest), "authority_epoch": 1}
    observed = {"planner_artifact_sha256": manifest["planner"]["artifact_sha256"],
                "planner_incarnation": "external-process", "runtime_generation": 1}
    probed = []
    monkeypatch.setattr(trial.pipeline, "probe_external_planner", lambda client, release: probed.append(release) or observed)
    monkeypatch.setattr(trial, "verify_versions", lambda: None)
    monkeypatch.setattr(trial, "verify_assets", lambda path: path == args.action_assets or pytest.fail("wrong asset path"))
    original_environment = {key: os.environ.get(key) for key in ("PYTHONPATH", "CONVOY_SMOLVLA_ASSETS")}
    original_authority = {path: path.read_bytes() for path in args.authority_dir.rglob("*") if path.is_file()}
    signing, probe = trial.authority(args.authority_dir)
    calls = []

    def pipeline_run(output, **options):
        calls.append(options)
        assert options["external_planner_url"] == args.planner_url
        assert options["execution_signing_keys_file"] == signing and options["planner_probe_token"] == probe
        assert options["manifest"] == manifest and options["planner_ca_file"] is None
        assert options["runtime_factory"] == "convoy_lerobot.runtime:smolvla"
        assert options["coordinator_module"] == "convoy_agent.coordinator.paired"
        assert "planner_command" not in options and "faults" not in options
        assert os.environ["PYTHONPATH"] == os.pathsep.join(map(str, trial.SOURCES))
        assert os.environ["CONVOY_SMOLVLA_ASSETS"] == str(args.action_assets)
        assert os.environ["HF_HUB_OFFLINE"] == "1"
        (output / "robot/coordinator").mkdir(parents=True)
        request = {"identity": identity, "request_id": "request", "observation_id": "observation",
                   "observation_digest": "c" * 64, "deadline_monotonic_ns": 1000000000}
        response = {**request, **observed, "decision": {"kind": "skill", "skill_id": SKILL_ID, "parameters": {}},
                    "planner_duration_ms": 1}
        with sqlite3.connect(output / "robot/coordinator/execution.sqlite3") as db:
            db.execute("CREATE TABLE plans (mission_id,request_json,result_json,state)")
            db.execute("INSERT INTO plans VALUES (?,?,?,?)", ("mission", json.dumps(request), json.dumps(response), "accepted"))
            db.execute("CREATE TABLE commands (mission_id,sequence,request_json,result_json,observation_json,state)")
            db.execute("INSERT INTO commands VALUES (?,?,?,?,?,?)", ("mission", 0,
                json.dumps({"sequence": 0, "observation": {"fixture": True}}),
                json.dumps({"action": [0, 0, 0, 0], "policy_duration_ms": 1}),
                json.dumps({"reward": 0, "success": True}), "applied"))
        result = {"status": "passed", "source_commit": "fixture-source", "source_dirty": False, "cases": [{
            "mission": {"id": "mission", "state": "completed"}, "episode": {"summary": {
                "steps": 1, "final_success": True, "planner_backend_kind": "llamacpp-text-model", "planner_accepted": True}}}]}
        (output / "pipeline-result.json").write_text(json.dumps(result))
        return result

    monkeypatch.setattr(trial.pipeline, "run", pipeline_run)
    if baseline_matches:
        assert trial.run(args)["status"] == "passed"
    else:
        with pytest.raises(RuntimeError, match="direct baseline"):
            trial.run(args)
    record = json.loads((args.output / "external-result.json").read_text())
    assert record["status"] == ("passed" if baseline_matches else "failed")
    assert record["direct_comparison"]["exact_actions_equal"] is baseline_matches
    assert record["plan"]["mission_id"] == "mission" and len(calls) == len(probed) == 1
    assert record["planner_ownership"] == "external"
    assert all(path.read_bytes() == raw for path, raw in original_authority.items())
    assert all(os.environ.get(key) == value for key, value in original_environment.items())
    assert probe not in json.dumps(record) and "PRIVATE KEY" not in json.dumps(record) and str(signing) not in json.dumps(record)
    before = (args.output / "external-result.json").read_bytes()
    with pytest.raises(FileExistsError):
        trial.run(args)
    assert (args.output / "external-result.json").read_bytes() == before and len(calls) == 1


def test_cli_parse_and_runtime_errors_never_echo_input(monkeypatch, capsys, tmp_path):
    marker = "private-input-marker"
    monkeypatch.setattr(sys, "argv", ["external_planner.py", "prepare", "--unknown", marker])
    with pytest.raises(SystemExit):
        trial.main()
    assert marker not in capsys.readouterr().err
    monkeypatch.setattr(sys, "argv", ["external_planner.py", "prepare", "--authority-dir", str(tmp_path / "authority")])
    monkeypatch.setattr(trial, "prepare", lambda _: (_ for _ in ()).throw(ValueError(marker)))
    with pytest.raises(SystemExit):
        trial.main()
    assert marker not in capsys.readouterr().err
