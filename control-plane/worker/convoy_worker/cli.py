from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path

import uvicorn

from .app import create_app


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
    runtime = getattr(importlib.import_module(module), name)()
    app = create_app(
        json.loads(args.release.read_text()), runtime,
        execution_secret=os.environ.get("CONVOY_EXECUTION_SECRET", ""),
        probe_token=os.environ.get("CONVOY_WORKER_PROBE_TOKEN", ""),
    )
    uvicorn.run(app, host=args.host, port=args.port, limit_concurrency=16, timeout_keep_alive=30)


if __name__ == "__main__":
    main()
