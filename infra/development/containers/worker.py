"""Local deployment TLS wrapper; the production worker application is unchanged."""

import json
import os
from pathlib import Path

import uvicorn
from convoy_sim.runtimes import scripted
from convoy_worker.app import create_app
from convoy_worker.cli import execution_options

if "CONVOY_ACTION_VERIFICATION_KEYS_FILE" not in os.environ:
    raise ValueError("managed inference requires public action verification keys")
authorization = execution_options()

app = create_app(
    json.loads(Path("/app/release.json").read_text()), scripted(),
    **authorization,
    probe_token=os.environ["CONVOY_WORKER_PROBE_TOKEN"],
)
uvicorn.run(app, host="0.0.0.0", port=8443, limit_concurrency=16,
            ssl_certfile="/run/tls/service.crt", ssl_keyfile="/run/tls/service.key")
