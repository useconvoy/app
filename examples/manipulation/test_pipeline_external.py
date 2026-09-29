"""Endpoint ownership and real-key distribution; no native model or simulator runs."""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pipeline
import pytest
from convoy_contracts.execution import canonical_digest
from convoy_contracts.grants import GrantVerifier, SigningKeys
from convoy_contracts.pairing import (
    CATALOG_SHA256,
    FIXED_TASK,
    PAIRED_PROFILE,
    PLANNER_PROTOCOL_SHA256,
    PLANNER_RUNTIME,
    SKILL_ID,
)
from convoy_lerobot.artifact import reference_manifest
from development_signing import development_grants

PROBE = "private-planner-probe-" + "x" * 40
IDENTITY = {"robot_id": "robot", "device_id": "device", "mission_id": "mission", "boot_id": "boot",
            "incarnation": "coordinator", "release_digest": "a" * 64, "authority_epoch": 1}


@pytest.fixture
def manifest():
    return {"schema_version": 2, "profile": PAIRED_PROFILE, "action_manifest": reference_manifest(),
            "planner": {"runtime": PLANNER_RUNTIME, "artifact_sha256": "b" * 64,
                        "protocol_sha256": PLANNER_PROTOCOL_SHA256},
            "task": {"instruction": FIXED_TASK, "skill_id": SKILL_ID}, "catalog_sha256": CATALOG_SHA256,
            "planning": {"timeout_ms": 30000},
            "placement": {"policy": "development-local-cpu", "planner": "development-local"}}


@pytest.fixture
def authority(tmp_path):
    setup = tmp_path / "authority"
    setup.mkdir(mode=0o700)
    api, public = development_grants(setup)
    return Path(api["CONVOY_EXECUTION_SIGNING_KEYS_FILE"]), public


def inputs(manifest, authority):
    return {"manifest": manifest, "planner_evidence": "network wiring fixture", "planner_backend_kind": "llamacpp-text-model",
            "external_planner_url": "https://planner.example.test:9443", "planner_probe_token": PROBE,
            "execution_signing_keys_file": authority[0], "coordinator_module": "convoy_agent.coordinator.paired"}


@pytest.mark.parametrize("fault", ["both-endpoints", "no-signer", "no-probe", "remote-http", "missing-ca", "missing-signer", "ca-with-command"])
def test_invalid_configuration_refuses_before_creating_state_or_children(manifest, authority, tmp_path, monkeypatch, fault):
    args = inputs(manifest, authority)
    if fault == "both-endpoints":
        args["planner_command"] = ("convoy_planner.cli", "serve")
    elif fault == "no-signer":
        args.pop("execution_signing_keys_file")
    elif fault == "no-probe":
        args.pop("planner_probe_token")
    elif fault == "remote-http":
        args["external_planner_url"] = "http://planner.example.test:8080"
    elif fault == "missing-ca":
        args["planner_ca_file"] = tmp_path / "missing-ca.pem"
    elif fault == "missing-signer":
        args["execution_signing_keys_file"] = tmp_path / "missing-private.json"
    else:
        args.pop("external_planner_url")
        args.pop("planner_probe_token")
        args.update(planner_command=("convoy_planner.cli", "serve"), planner_ca_file=tmp_path / "ca.pem")
    monkeypatch.setattr(pipeline.subprocess, "Popen", lambda *_a, **_k: pytest.fail("configuration launched a child"))
    output = tmp_path / "run"
    with pytest.raises((ValueError, OSError)):
        pipeline.run(output, **args)
    assert not output.exists()


@pytest.mark.parametrize("field,value", [("ready", False), ("release_digest", "c" * 64),
                                         ("planner_artifact_sha256", "c" * 64), ("planner_incarnation", ""),
                                         ("runtime_generation", 0)])
def test_external_probe_requires_exact_release_and_valid_serving_identity(manifest, field, value):
    reply = {"ready": True, "release_digest": canonical_digest(manifest), "profile": PAIRED_PROFILE,
             "runtime": PLANNER_RUNTIME, "planner_artifact_sha256": "b" * 64,
             "planner_incarnation": "serving-container", "runtime_generation": 1, field: value}

    class Client:
        def probe(self, digest, profile):
            assert digest == canonical_digest(manifest) and profile == PAIRED_PROFILE
            return reply

    with pytest.raises(ValueError):
        pipeline.probe_external_planner(Client(), manifest)


@pytest.mark.parametrize("mode", ["external-signed", "owned-signed", "owned-legacy"])
def test_child_scopes_and_cleanup_never_own_external_endpoint(manifest, authority, tmp_path, monkeypatch, mode):
    """Stop at coordinator launch; capture real harness wiring without model work."""
    args = inputs(manifest, authority)
    if mode != "external-signed":
        for name in ("external_planner_url", "planner_probe_token"):
            args.pop(name)
        args["planner_command"] = ("convoy_planner.cli", "serve")
    if mode == "owned-legacy":
        args.pop("execution_signing_keys_file")
    else:
        assert SigningKeys(authority[0]).verification_document("planner") == json.loads(
            Path(authority[1]["CONVOY_PLANNER_VERIFICATION_KEYS_FILE"]).read_text())
    if mode == "external-signed":
        args["planner_ca_file"] = tmp_path / "explicit-ca.pem"  # transport is a fixture below
    for key in pipeline.EXECUTION_ENV:
        monkeypatch.setenv(key, "inherited-authority-must-not-leak")
    launched, probes, health_urls = [], [], []
    stopped = []

    class StopAtCoordinator(RuntimeError):
        pass

    class Process:
        def __init__(self, command, *, env, **_):
            launched.append((command, env))
            self.command, self.code = command, None
            if command[2] == args["coordinator_module"]:
                raise StopAtCoordinator("wiring captured before any mission")

        def poll(self):
            return self.code

        def terminate(self):
            stopped.append(self.command[2])
            self.code = 0

        def wait(self, **_):
            return self.code

    class Planner:
        def __init__(self, url, token, *, ca_file):
            assert url == args["external_planner_url"] and token == PROBE and ca_file == args["planner_ca_file"]

        def probe(self, release, profile):
            probes.append((release, profile))
            return {"ready": True, "release_digest": release, "profile": profile, "runtime": PLANNER_RUNTIME,
                    "planner_artifact_sha256": "b" * 64, "planner_incarnation": "actual-serving-process",
                    "runtime_generation": 3}

    class API:
        def __init__(self, **_):
            self.headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def post(self, path, **_):
            body = {"id": "fixture", "token": "enrollment-only", "digest": canonical_digest(manifest), "generation": 1}
            return httpx.Response(200, json=body, request=httpx.Request("POST", "http://127.0.0.1" + path))

        def get(self, path):
            return httpx.Response(200, json={}, request=httpx.Request("GET", "http://127.0.0.1" + path))

    def health(url):
        health_urls.append(url)
        return httpx.Response(200)

    monkeypatch.setattr(pipeline, "PlannerHTTP", Planner)
    monkeypatch.setattr(pipeline.subprocess, "Popen", Process)
    monkeypatch.setattr(pipeline.subprocess, "check_output", lambda *_a, **_k: "")
    monkeypatch.setattr(pipeline.httpx, "Client", API)
    monkeypatch.setattr(pipeline.httpx, "get", health)
    monkeypatch.setattr(pipeline, "enroll", lambda *_a, **_k: {"device_id": "device"})
    original_private = authority[0].read_bytes()
    output = tmp_path / "run"
    with pytest.raises(StopAtCoordinator):
        pipeline.run(output, **args)
    by_module = {command[2]: (command, env) for command, env in launched}
    assert len(by_module) == (3 if mode == "external-signed" else 4)
    assert set(stopped) == {name for name in by_module if name != args["coordinator_module"]}
    api_env = by_module["convoy_server.cli"][1]
    worker_env = by_module["convoy_worker.cli"][1]
    command, coordinator_env = by_module[args["coordinator_module"]]
    assert (pipeline.EXECUTION_ENV | pipeline.PROBE_ENV) & coordinator_env.keys() == pipeline.PROBE_ENV
    assert pipeline.PROBE_ENV.isdisjoint(api_env)
    assert "CONVOY_PLANNER_PROBE_TOKEN" not in worker_env
    assert not any("inherited-authority" in value for _, env in launched for value in env.values())
    if mode == "external-signed":
        assert probes == [(canonical_digest(manifest), PAIRED_PROFILE)]
        assert command[command.index("--planner-url") + 1] == args["external_planner_url"]
        assert command[command.index("--planner-ca-file") + 1] == str(args["planner_ca_file"].absolute())
        assert all(args["external_planner_url"] not in url for url in health_urls)
        assert not (output / "verification/planner.json").exists()
    if mode != "owned-legacy":
        assert pipeline.EXECUTION_ENV & api_env.keys() == {"CONVOY_EXECUTION_SIGNING_KEYS_FILE"}
        assert pipeline.EXECUTION_ENV & worker_env.keys() == {"CONVOY_ACTION_VERIFICATION_KEYS_FILE"}
        expiry = time.time() + 60
        token = SigningKeys(authority[0]).sign(IDENTITY, "action", expiry)
        verifier = GrantVerifier(worker_env["CONVOY_ACTION_VERIFICATION_KEYS_FILE"], purpose="action")
        assert verifier.verify(token) == {**IDENTITY, "expires_at": expiry}
        if mode == "owned-signed":
            planner_env = by_module["convoy_planner.cli"][1]
            assert pipeline.EXECUTION_ENV & planner_env.keys() == {"CONVOY_PLANNER_VERIFICATION_KEYS_FILE"}
            assert "CONVOY_WORKER_PROBE_TOKEN" not in planner_env
    else:
        assert pipeline.EXECUTION_ENV & api_env.keys() == {"CONVOY_EXECUTION_SECRET", "CONVOY_PLANNER_EXECUTION_SECRET"}
        assert worker_env["CONVOY_EXECUTION_SECRET"] == api_env["CONVOY_EXECUTION_SECRET"]
        assert by_module["convoy_planner.cli"][1]["CONVOY_PLANNER_EXECUTION_SECRET"] == api_env["CONVOY_PLANNER_EXECUTION_SECRET"]
    assert authority[0].read_bytes() == original_private
    serialized = (output / "pipeline-result.json").read_text()
    assert PROBE not in serialized and "PRIVATE KEY" not in serialized and str(authority[0]) not in serialized


def test_shared_authority_never_replaces_existing_keys_and_jobs_receive_none(authority):
    path, _ = authority
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        development_grants(path.parents[1])
    assert path.read_bytes() == original
    env = {key: "sensitive" for key in pipeline.EXECUTION_ENV | pipeline.PROBE_ENV}
    env.update(CONVOY_ADMIN_EMAIL="private", CONVOY_ADMIN_PASSWORD="private", DATABASE_URL="database-needed")
    assert pipeline.process_environment(env, role="jobs") == {"DATABASE_URL": "database-needed"}
