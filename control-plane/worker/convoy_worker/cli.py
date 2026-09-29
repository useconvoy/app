from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path

import uvicorn
from convoy_contracts.grants import GrantVerifier

from .app import create_app


def execution_options() -> dict:
    """Select exactly one trusted verifier configuration before loading a model."""
    if "CONVOY_EXECUTION_SIGNING_KEYS_FILE" in os.environ:
        raise ValueError("private execution signing keys must not be configured in a model process")
    path = os.environ.get("CONVOY_ACTION_VERIFICATION_KEYS_FILE")
    secret = os.environ.get("CONVOY_EXECUTION_SECRET")
    if path is not None:
        if any(name in os.environ for name in ("CONVOY_EXECUTION_SECRET", "CONVOY_PLANNER_EXECUTION_SECRET")):
            raise ValueError("action verification keys and either role's HMAC secret are mutually exclusive")
        return {"grant_verifier": GrantVerifier(Path(path), purpose="action")}
    if secret is None or len(secret.encode()) < 32:
        raise ValueError("configure action verification keys or an HMAC secret of at least 32 characters")
    return {"execution_secret": secret}


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve one pinned policy release")
    parser.add_argument("--release", required=True, type=Path)
    parser.add_argument("--factory", required=True, help="trusted installed module:factory (no remote imports)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8091)
    args = parser.parse_args()
    module, separator, name = args.factory.partition(":")
    if not separator or not module or not name:
        parser.error("factory must be module:factory")
    try:
        authorization = execution_options()
    except ValueError as error:
        parser.error(str(error))
    runtime = getattr(importlib.import_module(module), name)()
    app = create_app(
        json.loads(args.release.read_text()), runtime,
        **authorization,
        probe_token=os.environ.get("CONVOY_WORKER_PROBE_TOKEN", ""),
    )
    uvicorn.run(app, host=args.host, port=args.port, limit_concurrency=16, timeout_keep_alive=30)


if __name__ == "__main__":
    main()
