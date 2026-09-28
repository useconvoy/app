"""Bind actual admitted model/runtime and the executing adapter implementation."""

from __future__ import annotations

import hashlib
import sys
from importlib.metadata import version
from pathlib import Path

from convoy_contracts import execution, pairing
from convoy_contracts.execution import _digest, _integer, _keys, _text, canonical_digest
from convoy_contracts.pairing import CATALOG, PLANNER_PROTOCOL, PLANNER_RUNTIME

STATIC_GATEWAY_FIELDS = {"model_sha256", "runtime_artifact_sha256", "binary_sha256", "template_sha256",
                         "config_sha256", "implementation_sha256", "simulated"}
DYNAMIC_GATEWAY_FIELDS = {"release_id", "runtime_generation", "gateway_incarnation", "gateway_epoch"}


def validate_gateway_identity(value: dict) -> dict:
    _keys(value, STATIC_GATEWAY_FIELDS | DYNAMIC_GATEWAY_FIELDS, "gateway runtime identity")
    if value["simulated"] is not False:
        raise ValueError("the planner requires an actual loaded text model")
    for key in STATIC_GATEWAY_FIELDS - {"implementation_sha256", "simulated"}:
        _digest(value[key], key)
    legacy_sources = {"gateway.py", "runtime.py"}
    owned_sources = legacy_sources | {"owned_process.py", "runtime_args.py", "psutil-7.2.2"}
    sources = value["implementation_sha256"]
    if not isinstance(sources, dict) or set(sources) not in (legacy_sources, owned_sources):
        raise ValueError("unsupported gateway implementation identity")
    for digest in value["implementation_sha256"].values():
        _digest(digest, "gateway source")
    for field in ("release_id", "gateway_incarnation"):
        _text(value[field], field)
    for field in ("runtime_generation", "gateway_epoch"):
        _integer(value[field], field, 1, 2**53 - 1)
    return value


def implementation_sources() -> dict[str, Path]:
    package = Path(__file__).parent
    return {**{f"convoy_planner/{name}": package / name for name in (
        "app.py", "artifact.py", "backend.py", "protocol.py", "sessions.py", "controlled.py",
    )}, "convoy_contracts/pairing.py": Path(pairing.__file__),
            "convoy_contracts/execution.py": Path(execution.__file__)}


def artifact_descriptor(identity: dict, sources: dict[str, Path] | None = None,
                        *, fingerprints: dict[str, str] | None = None) -> dict:
    validate_gateway_identity(identity)
    return {"schema_version": 1, "runtime": PLANNER_RUNTIME, "protocol": PLANNER_PROTOCOL, "catalog": CATALOG,
            "python": ".".join(map(str, sys.version_info[:3])),
            "packages": {name: version(name) for name in ("fastapi", "pydantic", "uvicorn")},
            "gateway": {key: identity[key] for key in STATIC_GATEWAY_FIELDS},
            "implementation_sha256": (fingerprints if fingerprints is not None else
                {key: hashlib.sha256(path.read_bytes()).hexdigest()
                 for key, path in (sources or implementation_sources()).items()})}


def artifact_digest(identity: dict) -> str:
    return canonical_digest(artifact_descriptor(identity))
