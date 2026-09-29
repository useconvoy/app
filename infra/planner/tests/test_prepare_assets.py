"""Integrity and no-overwrite checks using small, real tar archives."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "prepare_assets.py"
SPEC = importlib.util.spec_from_file_location("prepare_assets", SCRIPT)
assets = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(assets)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value))
    return path


def archive(path, *, extra=None, omit_library=False):
    with tarfile.open(path, "w:gz") as bundle:
        members = {"bin/llama-server": b"native-binary", "build-manifest.json": b"{}"}
        if not omit_library:
            members["lib/libexample.so.0"] = b"native-library"
        for name, data in members.items():
            member = tarfile.TarInfo(name)
            member.mode = 0o755 if name.startswith("bin/") else 0o644
            member.size = len(data)
            bundle.addfile(member, io.BytesIO(data))
        if extra:
            bundle.addfile(extra)


@pytest.fixture
def inputs(tmp_path):
    model = tmp_path / "existing-model.gguf"
    model.write_bytes(b"small-pinned-model")
    runtime = tmp_path / "native.tar.gz"
    archive(runtime)
    native = {"schema_version": 1, "source": assets.SOURCE.copy(),
              "host": {"system": "Linux", "machine": "aarch64", "libc": "glibc 2.36"},
              "archive": {"path": str(runtime), "sha256": digest(runtime.read_bytes())},
              "binary": {"path": "/build/native/bin/llama-server", "sha256": digest(b"native-binary")},
              "libraries": [{"path": "/build/native/lib/libexample.so.0", "sha256": digest(b"native-library")}],
              "runtime_root": "/build/native", "validation": {"version_passed": True},
              "build_manifest": "/build/private/build-manifest.json"}
    old = {"schema_version": 1, "model": {"path": str(model), "sha256": digest(model.read_bytes()),
                                          "bytes": model.stat().st_size},
           "host": {"system": "Darwin", "machine": "arm64"}, "runtime_root": "old-Darwin-runtime",
           "binary": {"private": "must-not-copy"}, "validation": {"paths": ["sensitive-old-host-path"]}}
    return write_json(tmp_path / "native.json", native), write_json(tmp_path / "old.json", old), native, old


def originals(inputs):
    native_file, model_file, native, model = inputs
    paths = [native_file, model_file, Path(native["archive"]["path"]), Path(model["model"]["path"])]
    return [(p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in paths]


@pytest.mark.parametrize("existing", [False, True])
def test_curates_verified_assets_and_rebases_only_linux_runtime(inputs, tmp_path, existing):
    native_file, model_file, native, model = inputs
    output = tmp_path / "context"
    if existing:
        output.mkdir(mode=0o700)
    before = originals(inputs)
    result = assets.prepare_assets(native_file, model_file, output)
    assert {p.name for p in output.iterdir()} == {"model.gguf", "runtime.tar.gz", "assets.json"}
    assert result == json.loads((output / "assets.json").read_text())
    assert result["model"] == {**model["model"], "path": "/opt/convoy/assets/model.gguf"}
    assert result["runtime_root"] == "/opt/convoy/native-origin"
    assert result["binary"]["path"] == "/opt/convoy/native-origin/bin/llama-server"
    assert result["libraries"][0]["path"] == "/opt/convoy/native-origin/lib/libexample.so.0"
    assert result["archive"] == {**native["archive"], "path": "/opt/convoy/assets/runtime.tar.gz"}
    assert result["host"] == {"system": "Linux", "machine": "aarch64"}
    assert "Darwin" not in json.dumps(result) and "/build/" not in json.dumps(result)
    assert digest((output / "model.gguf").read_bytes()) == model["model"]["sha256"]
    assert digest((output / "runtime.tar.gz").read_bytes()) == native["archive"]["sha256"]
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in output.iterdir())
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert originals(inputs) == before


@pytest.mark.parametrize("kind", ["nonempty", "symlink", "public"])
def test_refuses_existing_or_unsafe_output_without_changing_it(inputs, tmp_path, kind):
    output = tmp_path / "context"
    original = tmp_path / "original"
    original.mkdir(mode=0o700)
    if kind == "symlink":
        output.symlink_to(original, target_is_directory=True)
    else:
        output.mkdir(mode=0o700)
        if kind == "nonempty":
            (output / "model.gguf").write_bytes(b"existing-user-file")
        else:
            output.chmod(0o755)
    before = [(p.name, p.read_bytes()) for p in output.iterdir()]
    with pytest.raises(ValueError):
        assets.prepare_assets(inputs[0], inputs[1], output)
    assert before == [(p.name, p.read_bytes()) for p in output.iterdir()]
    assert output.is_symlink() == (kind == "symlink")


@pytest.mark.parametrize("fault", ["commit", "dirty", "platform", "duplicate", "oversized", "bool-schema"])
def test_rejects_invalid_receipts_before_writing_context(inputs, tmp_path, fault):
    native_file, model_file, native, _ = inputs
    if fault == "commit":
        native["source"]["commit"] = "0" * 40
    elif fault == "dirty":
        native["source"]["dirty"] = True
    elif fault == "platform":
        native["host"]["machine"] = "x86_64"
    elif fault == "bool-schema":
        native["schema_version"] = True
    write_json(native_file, native)
    if fault == "duplicate":
        native_file.write_text('{"schema_version":1,"schema_version":1,"private":"not-in-error"}')
    elif fault == "oversized":
        native_file.write_bytes(b"sensitive" * assets.MAX_RECEIPT)
    output = tmp_path / "context"
    with pytest.raises(ValueError) as error:
        assets.prepare_assets(native_file, model_file, output)
    assert len(str(error.value)) < 160 and "sensitive" not in str(error.value)
    assert not output.exists()


@pytest.mark.parametrize("fault", ["model", "archive", "binary-pin", "library-pin", "missing-library", "traversal", "symlink"])
def test_rejects_changed_assets_and_incomplete_or_unsafe_archives(inputs, tmp_path, fault):
    native_file, model_file, native, model = inputs
    runtime = Path(native["archive"]["path"])
    if fault == "model":
        Path(model["model"]["path"]).write_bytes(b"x" * model["model"]["bytes"])
    elif fault == "archive":
        runtime.write_bytes(b"changed")
    elif fault == "binary-pin":
        native["binary"]["sha256"] = "0" * 64
    elif fault == "library-pin":
        native["libraries"][0]["sha256"] = "0" * 64
    else:
        extra = None
        if fault in {"traversal", "symlink"}:
            extra = tarfile.TarInfo("../outside" if fault == "traversal" else "lib/link")
            if fault == "symlink":
                extra.type, extra.linkname = tarfile.SYMTYPE, "/outside"
        archive(runtime, extra=extra, omit_library=fault == "missing-library")
        native["archive"]["sha256"] = digest(runtime.read_bytes())
    write_json(native_file, native)
    before = originals(inputs)
    output = tmp_path / "context"
    with pytest.raises(ValueError):
        assets.prepare_assets(native_file, model_file, output)
    assert not output.exists() or not list(output.iterdir())
    assert originals(inputs) == before
    assert not (tmp_path / "outside").exists()


def test_cli_runs_without_installed_project_packages_and_reports_no_host_paths(inputs, tmp_path):
    output = tmp_path / "context"
    command = [sys.executable, "-I", str(SCRIPT), "--native-receipt", str(inputs[0]),
               "--model-receipt", str(inputs[1]), "--output", str(output)]
    success = subprocess.run(command, capture_output=True, text=True, check=False)
    assert success.returncode == 0, success.stderr
    # A repeat cannot replace the already completed context.
    before = [(p.name, p.read_bytes()) for p in output.iterdir()]
    failure = subprocess.run(command, capture_output=True, text=True, check=False)
    assert failure.returncode == 2
    assert str(tmp_path) not in failure.stderr and "Traceback" not in failure.stderr
    assert before == [(p.name, p.read_bytes()) for p in output.iterdir()]


def test_rechecks_copied_bytes_before_publishing_receipt(inputs, tmp_path, monkeypatch):
    original_fsync = os.fsync
    damaged = False

    def damage_first_copy(fd):
        nonlocal damaged
        if not damaged:
            os.pwrite(fd, b"x", 0)
            damaged = True
        original_fsync(fd)

    monkeypatch.setattr(assets.os, "fsync", damage_first_copy)
    before = originals(inputs)
    output = tmp_path / "context"
    with pytest.raises(ValueError):
        assets.prepare_assets(inputs[0], inputs[1], output)
    assert damaged and not list(output.iterdir())
    assert originals(inputs) == before
