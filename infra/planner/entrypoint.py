"""Consume bounded deployment inputs before entering the planner model owner."""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from convoy_contracts.execution import canonical_digest, canonical_json
from convoy_contracts.pairing import PAIRED_PROFILE, PLANNER_RUNTIME, validate_release_manifest
from execution_keys import EXECUTION_ENV, _create, _directory, materialize_execution_keys

RELEASE_JSON = "CONVOY_PLANNER_RELEASE_JSON"
RELEASE_SHA256 = "CONVOY_PLANNER_RELEASE_SHA256"
RELEASE_PATH = Path("/run/convoy/release.json")
MAX_RELEASE_BYTES = 16 * 1024


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate release field")
        result[key] = value
    return result


def _constant(_):
    raise ValueError("non-finite release value")


def release_arguments(arguments: list[str]) -> list[str]:
    """Choose exactly one release source; never replace a mounted/existing file."""
    raw = os.environ.pop(RELEASE_JSON, None)
    digest = os.environ.pop(RELEASE_SHA256, None)
    try:
        parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False, exit_on_error=False)
        parser.add_argument("--manifest", action="append", default=[])
        parsed, _ = parser.parse_known_args(arguments[1:])
        if len(parsed.manifest) > 1 or (arguments[0] == "inspect" and parsed.manifest):
            raise ValueError("conflicting release sources")
        if raw is None and digest is None:
            return arguments
        if raw is None or digest is None or arguments[0] != "serve" or parsed.manifest:
            raise ValueError("conflicting release sources")
        if not 0 < len(raw.encode("utf-8")) <= MAX_RELEASE_BYTES:
            raise ValueError("release exceeds its bound")
        manifest = validate_release_manifest(json.loads(raw, object_pairs_hook=_object, parse_constant=_constant))
        if manifest["profile"] != PAIRED_PROFILE or manifest["planner"]["runtime"] != PLANNER_RUNTIME:
            raise ValueError("injection requires the real paired planner")
        if manifest["placement"]["planner"] not in {"development-local", "development-remote-cpu"}:
            raise ValueError("injection requires a CPU planner placement")
        if digest != canonical_digest(manifest):
            raise ValueError("release digest mismatch")
        encoded = canonical_json(manifest)
        if len(encoded) > MAX_RELEASE_BYTES:
            raise ValueError("canonical release exceeds its bound")
        # Reuse the role-key wrapper's private-directory and durable O_EXCL writer.
        path = _directory(RELEASE_PATH.parent) / RELEASE_PATH.name
        _create(path, encoded, 0o600)
        return [*arguments, "--manifest", str(path)]
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError, argparse.ArgumentError):
        raise ValueError("planner release configuration unavailable") from None


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in {"inspect", "serve"}:
        raise ValueError("choose inspect or serve")
    arguments = release_arguments(sys.argv[1:])
    if sys.argv[1] == "serve":
        directory = Path(tempfile.mkdtemp(prefix="convoy-planner-verification-"))
        os.environ.update(materialize_execution_keys("planner", directory))
    elif EXECUTION_ENV.intersection(os.environ) or "CONVOY_PLANNER_PROBE_TOKEN" in os.environ:
        raise ValueError("inspection does not require execution credentials")
    os.execv(sys.executable, [sys.executable, "-m", "convoy_planner.owned", *arguments])


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError):
        raise SystemExit("Planner container configuration is unavailable") from None
