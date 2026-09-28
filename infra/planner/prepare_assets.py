"""Curate existing pinned assets into a fresh Docker named context; no downloads.

Only model.gguf, runtime.tar.gz and assets.json are written. The source model is
read, never moved or modified. Runtime extraction and verification are repeated
inside the image by convoy_planner.local_assets before any native process starts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import tarfile
from pathlib import Path, PurePosixPath

COMMIT = "5266f24da75dc449bd56cbed7addb9c8e4a6a73e"
SOURCE = {"repo": "https://github.com/ggml-org/llama.cpp", "commit": COMMIT, "dirty": False}
MAX_RECEIPT = 64 * 1024
MAX_RUNTIME = 256 * 1024 * 1024
MAX_MODEL = 8 * 1024 * 1024 * 1024
IMAGE_ASSETS = PurePosixPath("/opt/convoy/assets")
IMAGE_RUNTIME = PurePosixPath("/opt/convoy/native-origin")
NATIVE_FIELDS = {"schema_version", "source", "host", "archive", "binary", "libraries", "runtime_root"}


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate receipt key")
        result[key] = value
    return result


def _constant(_):
    raise ValueError("nonfinite receipt value")


def _open_regular(path: Path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("asset input must be a regular file")
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def _read_receipt(path: Path) -> dict:
    with _open_regular(path) as source:
        raw = source.read(MAX_RECEIPT + 1)
    if len(raw) > MAX_RECEIPT:
        raise ValueError("receipt exceeds its size bound")
    value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise ValueError("unsupported receipt schema")
    return value


def _path(value) -> Path:
    if not isinstance(value, str) or not value or len(value) > 4096 or "\0" in value:
        raise ValueError("invalid asset path")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("asset paths must be absolute without parent traversal")
    return path


def _record(value, *, model=False) -> dict:
    if not isinstance(value, dict) or set(value) != {"path", "sha256", *({"bytes"} if model else set())}:
        raise ValueError("unsupported asset record")
    _path(value["path"])
    digest = value["sha256"]
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("invalid asset digest")
    if model and (type(value["bytes"]) is not int or not 1 <= value["bytes"] <= MAX_MODEL):
        raise ValueError("invalid model size")
    return value.copy()


def _native(value: dict) -> tuple[dict, dict[str, str]]:
    if not NATIVE_FIELDS <= value.keys() or value.keys() - NATIVE_FIELDS - {"validation", "build_manifest"}:
        raise ValueError("unsupported native receipt")
    if value["source"] != SOURCE or value["source"].get("dirty") is not False:
        raise ValueError("native receipt requires the exact clean llama.cpp source")
    host = value["host"]
    if not isinstance(host, dict) or host.get("system") != "Linux" or host.get("machine") != "aarch64":
        raise ValueError("native receipt must describe Linux aarch64")
    root = _path(value["runtime_root"])
    archive, binary = _record(value["archive"]), _record(value["binary"])
    libraries = value["libraries"]
    if not isinstance(libraries, list) or len(libraries) > 64:
        raise ValueError("unsupported native library list")
    expected, rebased = {}, []
    for index, record in enumerate([binary, *libraries]):
        record = _record(record)
        relative = _path(record["path"]).relative_to(root)
        if (index == 0 and relative != Path("bin/llama-server")) or (
            index > 0 and (len(relative.parts) != 2 or relative.parts[0] != "lib")
        ):
            raise ValueError("unsupported native member path")
        name = relative.as_posix()
        if name in expected:
            raise ValueError("duplicate native member")
        expected[name] = record["sha256"]
        rebased.append({"path": str(IMAGE_RUNTIME / name), "sha256": record["sha256"]})
    # Build/validation receipts remain separate evidence. Do not copy host-local
    # paths, or any runtime/model fields from the old Darwin receipt, into images.
    result = {"schema_version": 1, "source": SOURCE.copy(),
              "host": {"system": "Linux", "machine": "aarch64"},
              "archive": {"path": str(IMAGE_ASSETS / "runtime.tar.gz"), "sha256": archive["sha256"]},
              "binary": rebased[0], "libraries": rebased[1:], "runtime_root": str(IMAGE_RUNTIME)}
    return result, expected


def _hash(stream) -> str:
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


def _verify_archive(path: Path, pin: str, expected: dict[str, str]) -> None:
    with _open_regular(path) as source:
        if not 1 <= os.fstat(source.fileno()).st_size <= MAX_RUNTIME or _hash(source) != pin:
            raise ValueError("runtime archive differs from its pin or size bound")
        source.seek(0)
        with tarfile.open(fileobj=source, mode="r:gz") as archive:
            seen, verified, total = set(), set(), 0
            for member in archive:
                total += member.size
                if (member.name in seen or len(seen) >= 1024 or not 0 <= total <= MAX_RUNTIME
                        or member.size < 0 or not (member.isfile() or member.isdir())):
                    raise ValueError("unsupported native archive member")
                seen.add(member.name)
                if member.isdir() and member.name in {"bin", "lib"}:
                    continue
                if not member.isfile() or member.name not in {*expected, "build-manifest.json"}:
                    raise ValueError("unexpected native archive member")
                if member.name in expected:
                    with archive.extractfile(member) as content:
                        if _hash(content) != expected[member.name]:
                            raise ValueError("native member differs from its receipt pin")
                    if member.name == "bin/llama-server" and not member.mode & 0o111:
                        raise ValueError("packaged native binary is not executable")
                    verified.add(member.name)
            if verified != expected.keys():
                raise ValueError("native archive lacks a pinned member")


def _copy_verified(source: Path, target: Path, pin: str, size: int | None, maximum: int, created: list[Path]):
    with _open_regular(source) as original:
        actual_size = os.fstat(original.fileno()).st_size
        if not 1 <= actual_size <= maximum or (size is not None and actual_size != size):
            raise ValueError("asset input differs from its size pin")
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created.append(target)
        with os.fdopen(fd, "wb") as destination:
            digest, copied = hashlib.sha256(), 0
            for block in iter(lambda: original.read(1024 * 1024), b""):
                copied += len(block)
                if copied > actual_size:
                    raise ValueError("asset changed during copy")
                digest.update(block)
                destination.write(block)
            destination.flush()
            os.fsync(destination.fileno())
        if copied != actual_size or digest.hexdigest() != pin:
            raise ValueError("asset input differs from its digest pin")
    with _open_regular(target) as copied_file:
        if os.fstat(copied_file.fileno()).st_size != actual_size or _hash(copied_file) != pin:
            raise ValueError("copied asset differs from its pin")


def prepare_assets(native_receipt: Path, model_receipt: Path, output: Path) -> dict:
    """Write one curated context, preserving existing files and all source assets."""
    created = []
    try:
        native, old_model = _read_receipt(native_receipt), _read_receipt(model_receipt)
        result, members = _native(native)
        model = _record(old_model.get("model"), model=True)
        result["model"] = {**model, "path": str(IMAGE_ASSETS / "model.gguf")}
        _verify_archive(_path(native["archive"]["path"]), native["archive"]["sha256"], members)
        try:
            output.mkdir(mode=0o700)
        except FileExistsError:
            pass
        info = output.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077
                or any(output.iterdir())):
            raise ValueError("output must be a new or empty private directory owned by this user")
        _copy_verified(_path(native["archive"]["path"]), output / "runtime.tar.gz",
                       native["archive"]["sha256"], None, MAX_RUNTIME, created)
        _copy_verified(_path(model["path"]), output / "model.gguf", model["sha256"], model["bytes"], MAX_MODEL, created)
        target = output / "assets.json"
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created.append(target)
        with os.fdopen(fd, "w") as destination:
            json.dump(result, destination, indent=2, sort_keys=True)
            destination.write("\n")
            destination.flush()
            os.fsync(destination.fileno())
        return result
    except (OSError, ValueError, TypeError, KeyError, EOFError, RecursionError, tarfile.TarError):
        for path in reversed(created):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass  # Retain incomplete private state if cleanup cannot finish.
        raise ValueError("asset context preparation failed: check receipts, pins and the private empty output directory") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-receipt", type=Path, required=True)
    parser.add_argument("--model-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        prepare_assets(args.native_receipt, args.model_receipt, args.output)
    except ValueError as error:
        parser.exit(2, f"{error}\n")
    print("Verified model.gguf, runtime.tar.gz and assets.json are ready for the named build context.")


if __name__ == "__main__":
    main()
