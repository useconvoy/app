# Jetson policy/physics timing experiment

This opt-in profile colocates the existing SmolVLA checkpoint and MetaWorld scene.
It does **not** modify the qualified CPU runtime, production agent, model release,
deployment gates or existing replay journal. Results are experiment artifacts,
not managed mission records. No physical-device action interface is present.

The first question is whether the Jetson can run the local loop. Cloud planner
integration follows only after this gate; the delay/drop flags below inject
**action-policy result delivery faults**, not cloud planning latency.

## Timing semantics

- The parent process owns MuJoCo and the camera. A spawned child owns inference.
  One request may be in flight; frames do not accumulate in a request queue.
- Physics advances at the environment's original 80 Hz action cadence while
  inference is pending. Absolute monotonic tick deadlines are never reset to hide
  drift. Large simulator lag ends the trial as `simulator_overrun`.
- Camera rendering and local IPC currently run on the parent. Their contention is
  included in physics lag. This is intentional qualification evidence; a renderer
  that overruns is a failed timing result, not a claimed real-time simulation.
- Chunk action times are anchored to the source observation, not result arrival.
  Expired prefixes are skipped. A new chunk replaces the valid future buffer;
  there is no blending, temporal ensembling or real-time chunking correction.
  This changes the original single-action CPU profile and is separately labelled.
- When the buffer expires, use zero normalized Cartesian delta and retain the
  last gripper command. Physics keeps advancing. This is a MetaWorld-only fallback,
  not a claim of safe physical stopping or grasp preservation.
- `lockstep` is an offline first-action reference: physics waits for inference.
  Do not interpret its task success as a timing qualification.
- Scripted policies use privileged state. Learned policies use the original
  480×480 corner2 camera, flipped on both axes, and four proprioceptive values.
- All seeds, including errors, remain in the task-success denominator. A timing
  pass currently means p95 physics lag at most one control period with no maximum
  lag abort. Task success, stale results, observation age and fallback use must
  also be reviewed; timing pass alone does not qualify the robot application.

## Isolated installation

Use a JetPack-compatible NVIDIA PyTorch base, resolved to an immutable digest.
The Dockerfile preserves NVIDIA's installed Torch/Torchvision versions and installs
the other experiment dependencies in an isolated virtual environment. The complete
resolved package list is saved inside the image at `/opt/experiment-packages.txt`.
The exact image ID and observed packages must accompany each qualification.

```sh
docker build --build-arg BASE_IMAGE=nvcr.io/nvidia/pytorch@sha256:<verified-digest> \
  -t convoy-jetson-timing:local integrations/lerobot/experiments/jetson_timing
```

Mount only the selected experiment checkout, verified public checkpoint assets
and a fresh results directory. Do not mount the production agent's data/config or
robot device nodes. Use NVIDIA's GPU runtime and no external networking for local
qualification. Memory/CPU limits protect host headroom, but a memory cap is not a
guarantee about every GPU/driver allocation on a shared-memory Jetson.

```sh
docker run --rm --init --runtime nvidia --network none \
  --memory 4500m --memory-swap 4500m --cpus 4 --shm-size 256m \
  -e CONVOY_EXPERIMENT_IMAGE=<image-id> \
  -v "$CHECKOUT:/repo:ro" -v "$ASSETS:/assets:ro" -v "$RESULTS:/results" \
  convoy-jetson-timing:local --mode calibrate --output /results/calibration
```

Then run new output directories for:

1. `--mode realtime --policy scripted --seeds 0,1,2` (physics/controller baseline).
2. `--mode lockstep --policy smolvla --chunk-size 1 --seeds 0` (hardware correctness).
3. `--mode realtime --policy smolvla --chunk-size 50 --seeds 0,1,2` (local overlap).
4. Same configuration with `--policy-delay-ms 100`, then `--drop-every 3`.

Measure before expanding the matrix. Failed timing gates should lead to a
diagnosis, not progressively relaxed thresholds. Rendering backend, CUDA version,
dtype and precision optimizations are experimental variables; do not silently
change them to make results resemble the CPU reference.

`--record` keeps at most 120 sampled camera frames in memory and writes PNGs after
the timed loop. `frames.json` contains actual capture times; playback must not
pretend these irregular captures have a uniform physical frame rate. Episodes
also contain per-action JSONL and a compact result. No telemetry upload is needed.

## Focused tests

```sh
PYTHONPATH=integrations/lerobot/experiments python -m unittest discover \
  -s integrations/lerobot/experiments -p 'test_*.py'
```

These cover late prefixes, duplicate/out-of-order results, expired buffers and
invalid commands. Actual physics, camera, CUDA and learned-policy checks are
bounded opt-in hardware runs, not checkpoint downloads on every PR.
