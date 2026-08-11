#!/usr/bin/env bash
# Launch the runtime worker + control plane on the host against the compose
# stack from `make e2e-up`, mirroring the runtime's own e2e environment.
# The website dev server and Playwright suite point at the control plane on
# :8700. Stop with: kill $(cat .dev-runtime.pids)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"
PYTHON_BIN="${CONVOY_DEV_PYTHON:-$REPO_ROOT/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "missing runtime Python at $PYTHON_BIN; create the repo .venv first" >&2
  exit 1
fi

export CONVOY_CODEC_KEY_B64="${CONVOY_CODEC_KEY_B64:-$(python3 -c 'import base64,os;print(base64.b64encode(os.urandom(32)).decode())')}"
export CONVOY_TEMPORAL_HOST="localhost:7233"
export CONVOY_TEMPORAL_NAMESPACE="default"
export CONVOY_PG_DSN="postgresql://convoy_app:convoy_app@localhost:5433/convoy"
export CONVOY_S3_ENDPOINT="http://localhost:9000"
export CONVOY_S3_BUCKET="convoy-artifacts"
export CONVOY_S3_ACCESS_KEY="convoy"
export CONVOY_S3_SECRET_KEY="convoy-secret-key"
export CONVOY_STUB_ENV_URL="${CONVOY_STUB_ENV_URL:-http://localhost:8780/gateway}"
export CONVOY_ENVIRONMENTS_INTERNAL_TOKEN="${CONVOY_ENVIRONMENTS_INTERNAL_TOKEN:-dev-internal-token-demo-profile-only}"
export CONVOY_DEV_TOKEN="${CONVOY_DEV_TOKEN:-e2e-dev-token}"
export CONVOY_TURN_EXECUTOR="${CONVOY_TURN_EXECUTOR:-scripted}"
export CONVOY_SCRIPTED_TURN_DELAY="${CONVOY_SCRIPTED_TURN_DELAY:-0.4}"
# The scripted executor never calls a model; leave LITELLM_* unset so the
# worker skips per-run virtual-key provisioning. Export both to point the
# stack at a running LiteLLM proxy for the pydantic_ai executor instead.

"$PYTHON_BIN" - <<'PY'
import asyncio
from convoy_runtime.providers.artifact_store import ArtifactStore

async def main() -> None:
    store = ArtifactStore(
        bucket="convoy-artifacts",
        endpoint_url="http://localhost:9000",
        access_key="convoy",
        secret_key="convoy-secret-key",
    )
    await store.ensure_bucket()

asyncio.run(main())
PY

"$PYTHON_BIN" -m convoy_runtime.worker &
WORKER_PID=$!
"$PYTHON_BIN" -m uvicorn convoy_runtime.control_plane.app:app \
  --host 127.0.0.1 --port 8700 --log-level warning &
CP_PID=$!
echo "$WORKER_PID $CP_PID" > "$REPO_ROOT/website/.dev-runtime.pids"
echo "worker pid $WORKER_PID, control plane pid $CP_PID (http://localhost:8700)"
wait
