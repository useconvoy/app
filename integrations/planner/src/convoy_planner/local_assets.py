"""Verify a trusted local text-runtime receipt without downloading or executing it.

The existing model remains in its content-pinned local cache. RuntimeSupervisor
rehashes it immediately before launch; the much smaller runtime is extracted into
a fresh private directory and every executable/library pin is checked there.
"""

from __future__ import annotations

import copy
import hashlib
import os
import stat
import tarfile
from pathlib import Path

from convoy_contracts.execution import _digest, _integer, _keys

MAX_RUNTIME_BYTES = 256 * 1024 * 1024
MAX_MODEL_BYTES = 8 * 1024 * 1024 * 1024
RECEIPT_FIELDS = {"schema_version", "source", "host", "archive", "binary", "libraries", "model", "runtime_root"}
RECEIPT_METADATA = {"validation", "model_source", "build_manifest"}


def local_path(value, name: str) -> Path:
    if not isinstance(value, str) or not value or len(value) > 4096 or "\0" in value:
        raise ValueError(f"{name} must be a bounded absolute local path")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{name} must be an absolute local path without parent traversal")
    return path


def bounded_json(value, *, depth: int = 0) -> None:
    """Bound receipt metadata as well as the fields that affect execution."""
    if depth > 16:
        raise ValueError("local recipe data is too deeply nested")
    if value is None or type(value) in (bool, int):
        return
    if type(value) is float:
        import math

        if not math.isfinite(value):
            raise ValueError("local recipe numbers must be finite")
        return
    if isinstance(value, str):
        if len(value) > 16384 or "\0" in value:
            raise ValueError("local recipe text exceeds its bound")
        return
    if isinstance(value, dict):
        if len(value) > 1024 or any(not isinstance(key, str) or len(key) > 256 for key in value):
            raise ValueError("local recipe object exceeds its bound")
        items = value.values()
    elif isinstance(value, list) and len(value) <= 1024:
        items = value
    else:
        raise ValueError("local recipe contains unsupported or oversized data")
    for item in items:
        bounded_json(item, depth=depth + 1)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_text_receipt(value: dict) -> dict:
    bounded_json(value)
    if (not isinstance(value, dict) or not RECEIPT_FIELDS <= value.keys()
            or value.keys() - RECEIPT_FIELDS - RECEIPT_METADATA):
        raise ValueError("unsupported local text asset receipt")
    _integer(value["schema_version"], "receipt schema_version", 1, 1)
    _keys(value["source"], {"repo", "commit", "dirty"}, "runtime source")
    source = value["source"]
    if (source["repo"] != "https://github.com/ggml-org/llama.cpp" or source["dirty"] is not False
            or not isinstance(source["commit"], str) or len(source["commit"]) != 40
            or any(c not in "0123456789abcdef" for c in source["commit"])):
        raise ValueError("runtime receipt requires a clean pinned llama.cpp source")
    if (not isinstance(value["host"], dict) or not {"system", "machine"} <= value["host"].keys()
            or any(not isinstance(value["host"][key], str) or not value["host"][key]
                   for key in ("system", "machine"))):
        raise ValueError("runtime receipt requires its build host platform")
    root = local_path(value["runtime_root"], "runtime_root")
    libraries = value["libraries"]
    if not isinstance(libraries, list) or len(libraries) > 64:
        raise ValueError("runtime library list exceeds its bound")
    seen = set()
    for kind, records in (("model", [value["model"]]), ("archive", [value["archive"]]),
                          ("binary", [value["binary"]]), ("library", libraries)):
        for record in records:
            _keys(record, {"path", "sha256", *({"bytes"} if kind == "model" else set())}, kind)
            path = local_path(record["path"], f"{kind} path")
            _digest(record["sha256"], f"{kind} sha256")
            if kind == "model":
                _integer(record["bytes"], "model bytes", 1, MAX_MODEL_BYTES)
            if kind in {"binary", "library"}:
                relative = path.relative_to(root)
                if kind == "binary" and relative != Path("bin/llama-server"):
                    raise ValueError("only the packaged bin/llama-server executable is supported")
                if kind == "library" and (len(relative.parts) != 2 or relative.parts[0] != "lib"):
                    raise ValueError("runtime libraries must be directly under lib/")
                if relative in seen:
                    raise ValueError("duplicate runtime member receipt")
                seen.add(relative)
    return copy.deepcopy(value)


def prepare_text_assets(receipt: dict, output: Path) -> tuple[Path, Path]:
    """Verify existing assets and extract output/runtime; output must already exist."""
    receipt = validate_text_receipt(receipt)
    output = Path(output).absolute()
    info = output.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("text staging directory must be private and owned by this user")
    model = Path(receipt["model"]["path"]).resolve(strict=True)
    if (not model.is_file() or model.stat().st_size != receipt["model"]["bytes"]
            or sha256(model) != receipt["model"]["sha256"]):
        raise ValueError("model bytes do not match the supplied pin")
    archive = Path(receipt["archive"]["path"]).resolve(strict=True)
    if (not archive.is_file() or archive.stat().st_size > MAX_RUNTIME_BYTES
            or sha256(archive) != receipt["archive"]["sha256"]):
        raise ValueError("runtime archive differs from the supplied pin or size bound")
    extracted = output / "runtime"
    extracted.mkdir(mode=0o700, exist_ok=False)
    with tarfile.open(archive) as source:
        members, names, total = [], set(), 0
        for member in source:
            total += member.size
            if (len(members) >= 1024 or total > MAX_RUNTIME_BYTES or member.size < 0
                    or len(member.name) > 1024 or member.name in names
                    or not (member.isfile() or member.isdir() or member.issym() or member.islnk())):
                raise ValueError("runtime archive members exceed the supported shape or bounds")
            names.add(member.name)
            members.append(member)
        source.extractall(extracted, members=members, filter="data")
    original_root = Path(receipt["runtime_root"])
    binary = None
    for record in [receipt["binary"], *receipt["libraries"]]:
        member = (extracted / Path(record["path"]).relative_to(original_root)).resolve(strict=True)
        member.relative_to(extracted.resolve())
        if not member.is_file() or sha256(member) != record["sha256"]:
            raise ValueError("extracted runtime member differs from the supplied pin")
        if record is receipt["binary"]:
            binary = member
    if binary is None or not os.access(binary, os.X_OK):
        raise ValueError("packaged llama-server is not executable")
    return model, binary
