"""Scripted CPU worker; TLS terminates at its separate AWS Application LB."""
import json
import os
from pathlib import Path

import uvicorn
from convoy_sim.runtimes import scripted
from convoy_worker.app import create_app

application = create_app(
    json.loads(Path("/app/release.json").read_text()), scripted(),
    execution_secret=os.environ["CONVOY_EXECUTION_SECRET"],
    probe_token=os.environ["CONVOY_WORKER_PROBE_TOKEN"],
)
uvicorn.run(application, host="0.0.0.0", port=8080, limit_concurrency=16)
