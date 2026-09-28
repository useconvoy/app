from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from convoy_contracts.execution import canonical_digest
from convoy_contracts.pairing import PLANNER_PROTOCOL_SHA256, PLANNER_RUNTIME

from .app import create_app
from .artifact import artifact_descriptor
from .backend import GatewayBackend


def main():
    parser = argparse.ArgumentParser(description="Bind the owned local text gateway to a paired release")
    parser.add_argument("mode", choices=("inspect", "serve"))
    parser.add_argument("--gateway-url", required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9101)
    args = parser.parse_args()
    backend = GatewayBackend(args.gateway_url)
    if args.mode == "inspect":
        identity = backend.inspect()
        descriptor = artifact_descriptor(identity)
        print(json.dumps({"planner": {"runtime": PLANNER_RUNTIME, "artifact_sha256": canonical_digest(descriptor),
                                      "protocol_sha256": PLANNER_PROTOCOL_SHA256},
                          "gateway_identity": identity, "artifact": descriptor}, indent=2))
        return
    if not args.manifest:
        parser.error("serve requires --manifest")
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("reference planner binds loopback; use an authenticated tunnel or qualified HTTPS proxy")
    execution_secret = os.environ["CONVOY_PLANNER_EXECUTION_SECRET"]
    if execution_secret == os.environ.get("CONVOY_EXECUTION_SECRET"):
        parser.error("planner and action execution keys must differ")
    app = create_app(json.loads(args.manifest.read_text()), backend, execution_secret=execution_secret,
                     probe_token=os.environ["CONVOY_PLANNER_PROBE_TOKEN"])
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
