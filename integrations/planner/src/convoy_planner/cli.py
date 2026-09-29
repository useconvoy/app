from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from convoy_contracts.execution import canonical_digest
from convoy_contracts.grants import GrantVerifier
from convoy_contracts.pairing import PLANNER_PROTOCOL_SHA256, PLANNER_RUNTIME

from .app import create_app
from .artifact import artifact_descriptor
from .backend import GatewayBackend


def execution_options() -> dict:
    """Public-only verifier mode is explicit; asymmetric failures never use HMAC."""
    if "CONVOY_EXECUTION_SIGNING_KEYS_FILE" in os.environ:
        raise ValueError("private execution signing keys must not be configured in a model process")
    path = os.environ.get("CONVOY_PLANNER_VERIFICATION_KEYS_FILE")
    secret = os.environ.get("CONVOY_PLANNER_EXECUTION_SECRET")
    if path is not None:
        if any(name in os.environ for name in ("CONVOY_EXECUTION_SECRET", "CONVOY_PLANNER_EXECUTION_SECRET")):
            raise ValueError("planner verification keys and either role's HMAC secret are mutually exclusive")
        return {"grant_verifier": GrantVerifier(Path(path), purpose="planner")}
    if secret is not None and secret == os.environ.get("CONVOY_EXECUTION_SECRET"):
        raise ValueError("planner and action execution keys must differ")
    if secret is None or len(secret.encode()) < 32:
        raise ValueError("configure planner verification keys or an HMAC secret of at least 32 characters")
    return {"execution_secret": secret}


def main():
    parser = argparse.ArgumentParser(description="Bind the owned local text gateway to a paired release")
    parser.add_argument("mode", choices=("inspect", "serve"))
    parser.add_argument("--gateway-url", required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9101)
    args = parser.parse_args()
    if "CONVOY_EXECUTION_SIGNING_KEYS_FILE" in os.environ:
        parser.error("private execution signing keys must not be configured in a model process")
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
    try:
        authorization = execution_options()
    except ValueError as error:
        parser.error(str(error))
    app = create_app(json.loads(args.manifest.read_text()), backend, **authorization,
                     probe_token=os.environ["CONVOY_PLANNER_PROBE_TOKEN"])
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
