"""Container key distribution and persistent initialization, using real key files."""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
import time
from pathlib import Path

import pytest
from convoy_contracts.grants import GrantVerifier, SigningKeys

SPEC = importlib.util.spec_from_file_location("execution_keys", Path(__file__).parents[1] / "execution_keys.py")
keys = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(keys)

IDENTITY = {"robot_id": "robot", "device_id": "device", "mission_id": "mission", "boot_id": "boot",
            "incarnation": "process", "release_digest": "a" * 64, "authority_epoch": 1}


def private_dir(path):
    path.mkdir(mode=0o700)
    return path


def contents(*paths):
    return [(path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns) for path in paths]


@pytest.fixture
def installation(tmp_path, monkeypatch):
    for name in keys.EXECUTION_ENV:
        monkeypatch.delenv(name, raising=False)
    signing = private_dir(tmp_path / "private") / "signing.json"
    public = private_dir(tmp_path / "public") / "action.json"
    keys.initialize(signing, public, "convoy-installation")
    return signing, public


def assert_bounded_error(error, *sensitive):
    text = str(error.value)
    assert len(text) < 160
    assert all(value not in text for value in sensitive)
    assert error.value.__suppress_context__


def test_initializer_preserves_existing_identity_bytes_and_permissions(installation):
    signing, public = installation
    initial = contents(signing, public)
    for existing in (False, True):
        keys.initialize(signing, public, "convoy-installation", require_existing=existing)
        assert contents(signing, public) == initial
    assert stat.S_IMODE(signing.stat().st_mode) == 0o600
    assert stat.S_IMODE(public.stat().st_mode) == 0o444
    assert "PRIVATE KEY" not in public.read_text()
    signer = SigningKeys(signing)
    assert signer.verification_document("action") == json.loads(public.read_text())
    expiry = time.time() + 60
    assert GrantVerifier(public, purpose="action").verify(signer.sign(IDENTITY, "action", expiry)) == {
        **IDENTITY, "expires_at": expiry,
    }


@pytest.mark.parametrize("role", ["api", "action", "planner"])
def test_materialization_consumes_only_own_json_and_returns_valid_role_path(installation, tmp_path, monkeypatch, role):
    signing, _ = installation
    signer = SigningKeys(signing)
    document = json.loads(signing.read_text()) if role == "api" else signer.verification_document(role)
    monkeypatch.setenv(keys.JSON_ENV[role], json.dumps(document))
    monkeypatch.setenv("CONVOY_WORKER_PROBE_TOKEN", "unrelated-probe-value")
    directory = private_dir(tmp_path / "task")
    result = keys.materialize_execution_keys(role, directory)
    assert result == {keys.FILE_ENV[role]: str(directory / "keys.json")}
    assert not keys.EXECUTION_ENV.intersection(os.environ)
    assert os.environ["CONVOY_WORKER_PROBE_TOKEN"] == "unrelated-probe-value"
    path = Path(result[keys.FILE_ENV[role]])
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    if role == "api":
        assert SigningKeys(path).verification_document("action") == signer.verification_document("action")
    else:
        expiry = time.time() + 60
        grant = GrantVerifier(path, purpose=role).verify(signer.sign(IDENTITY, role, expiry))
        assert grant["mission_id"] == IDENTITY["mission_id"]
        assert "PRIVATE KEY" not in path.read_text()
    # The injected credential was consumed; retries cannot silently reuse it.
    before = contents(path)
    with pytest.raises(ValueError):
        keys.materialize_execution_keys(role, directory)
    assert contents(path) == before


@pytest.mark.parametrize("conflict", sorted(keys.EXECUTION_ENV - {keys.JSON_ENV["action"]}))
def test_materialization_rejects_every_other_execution_setting_even_empty(installation, tmp_path, monkeypatch, conflict):
    signing, _ = installation
    document = SigningKeys(signing).verification_document("action")
    monkeypatch.setenv(keys.JSON_ENV["action"], json.dumps(document))
    monkeypatch.setenv(conflict, "")
    directory = private_dir(tmp_path / "task")
    with pytest.raises(ValueError) as error:
        keys.materialize_execution_keys("action", directory)
    assert_bounded_error(error, "PRIVATE KEY", str(directory))
    assert keys.JSON_ENV["action"] not in os.environ
    assert not list(directory.iterdir())


@pytest.mark.parametrize("fault", ["wrong-role", "private-as-public", "malformed", "oversized", "missing"])
def test_invalid_injected_document_never_produces_usable_configuration(installation, tmp_path, monkeypatch, fault):
    signing, _ = installation
    signer = SigningKeys(signing)
    raw = {
        "wrong-role": json.dumps(signer.verification_document("planner")),
        "private-as-public": signing.read_text(),
        "malformed": "sensitive-malformed-document",
        "oversized": "sensitive-document" * keys.MAX_FILE_BYTES,
    }.get(fault)
    if raw is not None:
        monkeypatch.setenv(keys.JSON_ENV["action"], raw)
    directory = private_dir(tmp_path / "task")
    with pytest.raises(ValueError) as error:
        keys.materialize_execution_keys("action", directory)
    assert_bounded_error(error, "sensitive", "PRIVATE KEY", str(directory))
    assert keys.JSON_ENV["action"] not in os.environ
    for path in directory.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    if fault in ("missing", "oversized"):
        assert not list(directory.iterdir())


def test_materialization_does_not_overwrite_existing_file_or_symlink(installation, tmp_path, monkeypatch):
    signing, _ = installation
    document = json.dumps(SigningKeys(signing).verification_document("action"))
    for mode in ("file", "symlink"):
        directory = private_dir(tmp_path / mode)
        target = directory / "keys.json"
        if mode == "file":
            target.write_text("original-sensitive-file")
            target.chmod(0o600)
            before = contents(target)
        else:
            target.symlink_to(signing)
            before = contents(signing)
        monkeypatch.setenv(keys.JSON_ENV["action"], document)
        with pytest.raises(ValueError) as error:
            keys.materialize_execution_keys("action", directory)
        assert_bounded_error(error, "original-sensitive", str(directory))
        assert contents(target if mode == "file" else signing) == before
        if mode == "symlink":
            assert target.is_symlink()


@pytest.mark.parametrize("missing", ["signing", "public", "both"])
def test_existing_installation_never_regenerates_missing_or_partial_keys(installation, missing):
    signing, public = installation
    if missing in ("signing", "both"):
        signing.unlink()
    if missing in ("public", "both"):
        public.unlink()
    surviving = [path for path in (signing, public) if path.exists()]
    before = contents(*surviving)
    for require_existing in ((True,) if missing == "both" else (False, True)):
        with pytest.raises(ValueError) as error:
            keys.initialize(signing, public, "convoy-installation", require_existing=require_existing)
        assert_bounded_error(error, str(signing), str(public))
        assert contents(*surviving) == before
        assert [path for path in (signing, public) if path.exists()] == surviving


@pytest.mark.parametrize("fault", ["issuer", "public-key", "private-permissions", "public-permissions", "dangling-link"])
def test_existing_pair_mismatch_or_unsafe_files_are_preserved_for_manual_recovery(installation, fault):
    signing, public = installation
    issuer = "different-issuer" if fault == "issuer" else "convoy-installation"
    if fault == "public-key":
        value = json.loads(public.read_text())
        value["keys"] = SigningKeys(signing).verification_document("planner")["keys"]
        public.chmod(0o600)
        public.write_text(json.dumps(value))
    elif fault == "private-permissions":
        signing.chmod(0o644)
    elif fault == "public-permissions":
        public.chmod(0o666)
    elif fault == "dangling-link":
        public.unlink()
        public.symlink_to(public.parent / "missing")
    surviving = [path for path in (signing, public) if path.is_file()]
    before = contents(*surviving)
    with pytest.raises(ValueError) as error:
        keys.initialize(signing, public, issuer)
    assert_bounded_error(error, str(signing), str(public))
    assert contents(*surviving) == before
    if fault == "dangling-link":
        assert public.is_symlink()


def test_private_directory_required_before_materialization(installation, tmp_path, monkeypatch):
    signing, _ = installation
    document = json.dumps(SigningKeys(signing).verification_document("action"))
    unsafe = private_dir(tmp_path / "unsafe")
    unsafe.chmod(0o755)
    link = tmp_path / "linked"
    link.symlink_to(signing.parent, target_is_directory=True)
    before = contents(signing)
    for directory in (unsafe, link, tmp_path / "missing"):
        monkeypatch.setenv(keys.JSON_ENV["action"], document)
        with pytest.raises(ValueError):
            keys.materialize_execution_keys("action", directory)
        assert not (directory / "keys.json").exists()
    assert contents(signing) == before


def test_initializer_cli_is_quiet_on_success_and_bounded_on_failure(installation, monkeypatch, capsys):
    signing, public = installation
    monkeypatch.setattr(sys, "argv", ["execution_keys", "initialize", "--signing-file", str(signing),
                                      "--action-verification-file", str(public), "--issuer", "convoy-installation",
                                      "--require-existing"])
    keys.main()
    output = capsys.readouterr()
    assert output.out == "Execution key pair verified; no key material emitted.\n" and output.err == ""
    signing.unlink()
    with pytest.raises(SystemExit) as error:
        keys.main()
    assert error.value.code == 2
    output = capsys.readouterr()
    assert "PRIVATE KEY" not in output.err and str(signing) not in output.err
    assert "preserve existing files" in output.err


def test_interrupted_initialization_preserves_private_key_and_refuses_silent_repair(tmp_path, monkeypatch):
    signing = private_dir(tmp_path / "private") / "signing.json"
    public = private_dir(tmp_path / "public") / "action.json"
    create = keys._create

    def fail_public(path, raw, mode):
        if path == public:
            raise OSError("sensitive-storage-diagnostic")
        create(path, raw, mode)

    with monkeypatch.context() as fault:
        fault.setattr(keys, "_create", fail_public)
        with pytest.raises(ValueError) as error:
            keys.initialize(signing, public, "convoy-installation")
        assert_bounded_error(error, "sensitive-storage-diagnostic")
    assert signing.exists() and not public.exists()
    before = contents(signing)
    SigningKeys(signing)  # A valid private identity was created before the interrupted publication.
    with pytest.raises(ValueError):
        keys.initialize(signing, public, "convoy-installation")
    assert contents(signing) == before and not public.exists()
