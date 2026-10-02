#!/usr/bin/env bash
# Reproduce the bimanual pill-task evidence: physics checks at 2 ms and 1 ms, still images, and the
# demo matrix (4 configurations x 3 slices x 3 seeds) recorded in the offline replay format, one
# directory per configuration, each checked with the import script's own reader (--dry-run).
#
#   scripts/pill_task_eval.sh OUTPUT_DIR [SEEDS] [JOBS]
#
# Import a configuration afterwards with
#   CONVOY_SERVER=… CONVOY_EMAIL=… CONVOY_PASSWORD=… python scripts/import_offline_eval.py OUTPUT_DIR/eval/<config>
#
# OUTPUT_DIR must not exist. Rendering needs OpenGL; without a display the script
# uses xvfb-run with MUJOCO_GL=glfw (set MUJOCO_GL=egl or osmesa to override).
set -euo pipefail
OUT="${1:?usage: pill_task_eval.sh OUTPUT_DIR [SEEDS] [JOBS]}"
SEEDS="${2:-0-2}"
JOBS="${3:-4}"
SLICES="${SLICES:-nominal,network_outage,pill_count_30}"
PREVIEWS="${PREVIEWS:-edge_qwen_edge_skills:network_outage:100,cloud_astra_only:network_outage:100}"
cd "$(dirname "$0")/.."
[ -e "$OUT" ] && { echo "$OUT exists; choose a new directory" >&2; exit 2; }
uv sync --frozen --extra video --extra managed
GL=()
if [ -z "${DISPLAY:-}" ] && [ -z "${MUJOCO_GL:-}" ] && command -v xvfb-run >/dev/null; then
  GL=(xvfb-run -a -s "-screen 0 1280x1024x24")
  export MUJOCO_GL=glfw
fi
mkdir -p "$OUT"
uv run --frozen convoy-sim-pills check-physics --output "$OUT/physics.json" > /dev/null
uv run --frozen convoy-sim-pills --timestep 0.001 check-physics --output "$OUT/physics_1ms.json" > /dev/null
${GL[@]+"${GL[@]}"} uv run --frozen convoy-sim-pills render --output "$OUT/stills"
${GL[@]+"${GL[@]}"} uv run --frozen convoy-sim-pills evaluate --configs all --slices "$SLICES" --seeds "$SEEDS" \
  --seed-stride 100 --jobs "$JOBS" --record all --replay offline --preview-camera photo --previews "$PREVIEWS" \
  --output "$OUT/eval"
for config in "$OUT"/eval/*/evaluation.json; do
  uv run --frozen python scripts/import_offline_eval.py --dry-run "$(dirname "$config")"
done
echo "results: $OUT/eval/results.md"
