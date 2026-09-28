"""Explicit deterministic planner fixture. There is no text-model inference here."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
from importlib.metadata import version
from pathlib import Path

from convoy_contracts.execution import _integer, _keys, _text
from convoy_contracts.pairing import CATALOG, CONTROLLED_PLANNER_RUNTIME, PLANNER_PROTOCOL, SKILL_ID

from .artifact import implementation_sources

BACKEND_KIND = "controlled-text-planner"
DECISION = {"kind": "skill", "skill_id": SKILL_ID, "parameters": {}}


def validate_controlled_identity(value):
    _keys(value, {"backend_kind", "backend_incarnation", "runtime_generation"}, "controlled planner identity")
    if value["backend_kind"] != BACKEND_KIND:
        raise ValueError("wrong controlled planner kind")
    _text(value["backend_incarnation"], "backend_incarnation")
    _integer(value["runtime_generation"], "runtime_generation", 1, 1)


def controlled_descriptor(*, fingerprints=None):
    return {"schema_version": 1, "runtime": CONTROLLED_PLANNER_RUNTIME, "backend_kind": BACKEND_KIND,
            "decision": DECISION, "protocol": PLANNER_PROTOCOL, "catalog": CATALOG,
            "scope": "deterministic fixed-skill admission; no text model, model weights or Jetson inference",
            "python": ".".join(map(str, sys.version_info[:3])),
            "packages": {name: version(name) for name in ("fastapi", "pydantic", "uvicorn")},
            "implementation_sha256": (fingerprints if fingerprints is not None else
                {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in implementation_sources().items()})}


class ControlledBackend:
    def __init__(self):
        self._identity = {"backend_kind": BACKEND_KIND, "backend_incarnation": secrets.token_hex(16),
                          "runtime_generation": 1}

    def inspect(self):
        return self._identity.copy()

    def propose(self, expected_identity, budget_s):
        if expected_identity != self._identity or budget_s <= 0:
            raise ValueError("controlled planner identity or deadline changed")
        return {"kind": "skill", "skill_id": SKILL_ID, "parameters": {}}


def main():
    from .app import create_app

    # `python -m` executes this file as __main__. Construct the canonical class
    # so the app's explicit controlled-backend gate sees the registered runtime.
    from .controlled import ControlledBackend as RegisteredBackend

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--port", type=int, default=9101)
    args = parser.parse_args()
    app = create_app(json.loads(args.manifest.read_text()), RegisteredBackend(),
                     execution_secret=os.environ["CONVOY_PLANNER_EXECUTION_SECRET"],
                     probe_token=os.environ["CONVOY_PLANNER_PROBE_TOKEN"])
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
