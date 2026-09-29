"""Release injection checks stop at exec; no container or model is launched."""

import importlib.util
import json
import os
import stat
import sys
from pathlib import Path

import pytest
from convoy_contracts.execution import VISUAL_PROFILE, canonical_digest, canonical_json
from convoy_contracts.grants import GrantVerifier
from convoy_contracts.pairing import (
    CATALOG_SHA256,
    CONTROLLED_PLANNER_RUNTIME,
    FIXED_TASK,
    PAIRED_PROFILE,
    PLANNER_PROTOCOL_SHA256,
    PLANNER_RUNTIME,
    SKILL_ID,
)
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


@pytest.fixture
def wrapper(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / "runtime"))
    spec = importlib.util.spec_from_file_location("planner_entrypoint", Path(__file__).parents[1] / "entrypoint.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in module.EXECUTION_ENV | {module.RELEASE_JSON, module.RELEASE_SHA256, "CONVOY_PLANNER_PROBE_TOKEN"}:
        monkeypatch.delenv(name, raising=False)
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    monkeypatch.setattr(module, "RELEASE_PATH", directory / "release.json")
    return module


@pytest.fixture
def manifest():
    return {"schema_version": 2, "profile": PAIRED_PROFILE,
        "action_manifest": {"schema_version": 1, "profile": VISUAL_PROFILE,
            "policy": {"runtime": "qualified-action", "artifact_sha256": "a" * 64},
            "environment": {"name": "pick-place-v3", "metaworld": "3.0.0", "mujoco": "3.3.0"},
            "execution": {"max_steps": 500, "decision_timeout_ms": 5000, "mission_timeout_s": 300}},
        "planner": {"runtime": PLANNER_RUNTIME, "artifact_sha256": "b" * 64,
                    "protocol_sha256": PLANNER_PROTOCOL_SHA256},
        "task": {"instruction": FIXED_TASK, "skill_id": SKILL_ID}, "catalog_sha256": CATALOG_SHA256,
        "planning": {"timeout_ms": 30000},
        "placement": {"policy": "development-local-cpu", "planner": "development-remote-cpu"}}


def inject(monkeypatch, wrapper, manifest):
    monkeypatch.setenv(wrapper.RELEASE_JSON, json.dumps(manifest))
    monkeypatch.setenv(wrapper.RELEASE_SHA256, canonical_digest(manifest))


@pytest.mark.parametrize("injected", [False, True])
def test_serve_preserves_file_mode_or_consumes_injection_before_exec(wrapper, manifest, monkeypatch, tmp_path, injected):
    arguments = ["entrypoint.py", "serve", "--assets", "/assets.json", "--output", "/state"]
    if injected:
        inject(monkeypatch, wrapper, manifest)
    else:
        wrapper.RELEASE_PATH.write_bytes(canonical_json(manifest))
        arguments += ["--manifest", str(wrapper.RELEASE_PATH)]
    public = Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    monkeypatch.setenv("CONVOY_PLANNER_VERIFICATION_JSON", json.dumps({"schema_version": 1,
        "issuer": "test-issuer", "audience": "planner-service", "purpose": "planner",
        "keys": [{"kid": "planner-1", "public_key_pem": public}]}))
    monkeypatch.setenv("CONVOY_PLANNER_PROBE_TOKEN", "probe-" * 8)
    keys = tmp_path / "verification"
    keys.mkdir(mode=0o700)
    monkeypatch.setattr(wrapper.tempfile, "mkdtemp", lambda **_: str(keys))
    monkeypatch.setattr(sys, "argv", arguments)
    executed = []
    monkeypatch.setattr(wrapper.os, "execv", lambda executable, argv: executed.append((executable, argv, dict(os.environ))))
    wrapper.main()
    assert len(executed) == 1
    executable, argv, environment = executed[0]
    assert executable == sys.executable and argv[:3] == [sys.executable, "-m", "convoy_planner.owned"]
    assert argv.count("--manifest") == 1 and argv[-1] == str(wrapper.RELEASE_PATH)
    assert json.loads(wrapper.RELEASE_PATH.read_bytes()) == manifest
    if injected:
        assert stat.S_IMODE(wrapper.RELEASE_PATH.stat().st_mode) == 0o600
    assert wrapper.RELEASE_JSON not in environment and wrapper.RELEASE_SHA256 not in environment
    assert "CONVOY_PLANNER_VERIFICATION_JSON" not in environment
    assert GrantVerifier(Path(environment["CONVOY_PLANNER_VERIFICATION_KEYS_FILE"]), purpose="planner").purpose == "planner"


@pytest.mark.parametrize("invalid", ["duplicate", "nonfinite", "oversized", "digest", "extra", "controlled", "action-only"])
def test_invalid_release_never_publishes_file_and_consumes_env(wrapper, manifest, monkeypatch, invalid):
    inject(monkeypatch, wrapper, manifest)
    if invalid == "duplicate":
        raw = json.dumps(manifest).replace('"runtime": "convoy-', '"runtime": "ignored", "runtime": "convoy-', 1)
        monkeypatch.setenv(wrapper.RELEASE_JSON, raw)
    elif invalid == "nonfinite":
        monkeypatch.setenv(wrapper.RELEASE_JSON, json.dumps(manifest).replace('"timeout_ms": 30000', '"timeout_ms": NaN'))
    elif invalid == "oversized":
        monkeypatch.setenv(wrapper.RELEASE_JSON, " " * wrapper.MAX_RELEASE_BYTES + json.dumps(manifest))
    elif invalid == "digest":
        monkeypatch.setenv(wrapper.RELEASE_SHA256, "0" * 64)
    else:
        if invalid == "extra":
            manifest["unexpected"] = "private-release-marker"
        elif invalid == "controlled":
            manifest["planner"]["runtime"] = CONTROLLED_PLANNER_RUNTIME
            manifest["placement"]["planner"] = "development-local-controlled"
        else:
            manifest = manifest["action_manifest"]
        inject(monkeypatch, wrapper, manifest)
    with pytest.raises(ValueError, match="^planner release configuration unavailable$") as error:
        wrapper.release_arguments(["serve"])
    assert error.value.__suppress_context__
    assert not wrapper.RELEASE_PATH.exists()
    assert wrapper.RELEASE_JSON not in os.environ and wrapper.RELEASE_SHA256 not in os.environ


@pytest.mark.parametrize("conflict", ["manifest", "default-manifest", "missing-json", "missing-digest", "inspect"])
def test_release_sources_cannot_mix_or_be_incomplete(wrapper, manifest, monkeypatch, conflict):
    inject(monkeypatch, wrapper, manifest)
    arguments = ["serve"]
    if conflict in {"manifest", "default-manifest"}:
        arguments += ["--manifest", "/mounted/release.json" if conflict == "manifest" else str(wrapper.RELEASE_PATH)]
    elif conflict == "missing-json":
        monkeypatch.delenv(wrapper.RELEASE_JSON)
    elif conflict == "missing-digest":
        monkeypatch.delenv(wrapper.RELEASE_SHA256)
    else:
        arguments = ["inspect"]
    with pytest.raises(ValueError, match="configuration unavailable"):
        wrapper.release_arguments(arguments)
    assert not wrapper.RELEASE_PATH.exists()
    assert wrapper.RELEASE_JSON not in os.environ and wrapper.RELEASE_SHA256 not in os.environ


@pytest.mark.parametrize("existing", ["regular", "symlink", "unsafe-parent"])
def test_release_never_overwrites_or_follows_links(wrapper, manifest, monkeypatch, tmp_path, existing):
    target = tmp_path / "existing"
    target.write_bytes(b"preserve this file")
    if existing == "regular":
        wrapper.RELEASE_PATH.write_bytes(b"preserve this file")
        target = wrapper.RELEASE_PATH
    elif existing == "symlink":
        wrapper.RELEASE_PATH.symlink_to(target)
    else:
        wrapper.RELEASE_PATH.parent.chmod(0o755)
    before = (target.read_bytes(), target.stat().st_ino, target.stat().st_mtime_ns)
    inject(monkeypatch, wrapper, manifest)
    with pytest.raises(ValueError, match="configuration unavailable"):
        wrapper.release_arguments(["serve"])
    assert (target.read_bytes(), target.stat().st_ino, target.stat().st_mtime_ns) == before
    if existing == "unsafe-parent":
        assert not wrapper.RELEASE_PATH.exists()


def test_inspection_needs_no_release_or_authority(wrapper, monkeypatch):
    executed = []
    monkeypatch.setattr(wrapper.os, "execv", lambda *args: executed.append(args))
    monkeypatch.setattr(sys, "argv", ["entrypoint.py", "inspect"])
    wrapper.main()
    assert len(executed) == 1 and not wrapper.RELEASE_PATH.exists()
    monkeypatch.setattr(sys, "argv", ["entrypoint.py", "inspect", "--manifest", "/mounted/release.json"])
    with pytest.raises(ValueError):
        wrapper.main()
    monkeypatch.setattr(sys, "argv", ["entrypoint.py", "inspect"])
    monkeypatch.setenv("CONVOY_PLANNER_PROBE_TOKEN", "")
    with pytest.raises(ValueError):
        wrapper.main()
    assert len(executed) == 1
