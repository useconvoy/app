"""Public-verification-only scripted CPU worker behind its separate AWS ALB."""
import json
import os
import sys
import tempfile
from pathlib import Path

import uvicorn
from convoy_contracts.grants import GrantVerifier
from convoy_sim.runtimes import scripted
from convoy_worker.app import create_app

sys.path.insert(0, "/app/runtime")
from execution_keys import materialize_execution_keys


def app():
    options = materialize_execution_keys("action", Path(tempfile.mkdtemp(prefix="convoy-verification-")))
    verifier = GrantVerifier(options["CONVOY_ACTION_VERIFICATION_KEYS_FILE"], purpose="action")
    return create_app(
        json.loads(Path("/app/release.json").read_text()), scripted(),
        grant_verifier=verifier, probe_token=os.environ.pop("CONVOY_WORKER_PROBE_TOKEN"),
    )


def main():
    try:
        application = app()
    except (KeyError, OSError, ValueError):
        raise SystemExit("AWS inference configuration is unavailable") from None
    uvicorn.run(application, host="0.0.0.0", port=8080, limit_concurrency=16)


if __name__ == "__main__":
    main()
