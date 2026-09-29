#!/usr/bin/env bash
# Local experiment launcher on the Jetson. No production agent mounts or changes.
set -euo pipefail
ROOT=${CONVOY_TIMING_ROOT:?set CONVOY_TIMING_ROOT to the experiment directory}
IMAGE=${CONVOY_TIMING_IMAGE:-convoy-jetson-timing:local}
RUN=${1:?unique run name}; shift
[[ "$RUN" =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$ ]] || { echo 'invalid run name' >&2; exit 2; }
test -d "$ROOT/repo/integrations/lerobot/experiments/jetson_timing"
test -f "$ROOT/assets/checkpoint/model.safetensors"
mkdir -p "$ROOT/results"
test ! -e "$ROOT/results/$RUN" || { echo 'run directory already exists' >&2; exit 2; }
IMAGE_ID=$(docker image inspect "$IMAGE" --format '{{.Id}}')
set +e
docker run --rm --init --name "convoy-timing-$RUN" --runtime nvidia --network none \
  --memory 4500m --memory-swap 4500m --cpus 4 --shm-size 256m \
  --cap-drop ALL --security-opt no-new-privileges:true --user "$(id -u):$(id -g)" \
  -e HOME=/tmp -e CUDA_CACHE_PATH=/tmp/cuda-cache -e CONVOY_EXPERIMENT_IMAGE="$IMAGE_ID" \
  -v "$ROOT/repo:/repo:ro" -v "$ROOT/assets:/assets:ro" -v "$ROOT/results:/results" \
  "$IMAGE" --output "/results/$RUN" "$@"
RESULT=$?
set -e
if [ "$RESULT" -ne 0 ]; then
  python3 - "$ROOT/results/$RUN" "$RESULT" <<'PY'
import json,sys
from pathlib import Path
root=Path(sys.argv[1]); root.mkdir(exist_ok=True)
path=root/'launcher-failure.json'
path.write_text(json.dumps({'docker_exit_code':int(sys.argv[2]), 'status':'failed'}, indent=2)+'\n')
PY
fi
exit "$RESULT"
