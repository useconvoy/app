"""Local deployment TLS wrapper; the production worker application is unchanged."""

import json
import os
from pathlib import Path

import uvicorn
from convoy_sim.runtimes import scripted
from convoy_worker.app import create_app

app = create_app(
    json.loads(Path("/app/release.json").read_text()), scripted(),
    execution_secret=os.environ["CONVOY_EXECUTION_SECRET"],
    probe_token=os.environ["CONVOY_WORKER_PROBE_TOKEN"],
)
uvicorn.run(app, host="0.0.0.0", port=8443, limit_concurrency=16,
            ssl_certfile="/run/tls/service.crt", ssl_keyfile="/run/tls/service.key")
