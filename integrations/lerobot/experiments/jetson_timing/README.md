# Jetson policy/physics timing experiment

This opt-in profile colocates the existing SmolVLA checkpoint and MetaWorld scene.
It does **not** modify the qualified CPU runtime, production agent, model release,
deployment gates or existing replay journal. Results are experiment artifacts,
not managed mission records. No physical-device action interface is present.

The first question is whether the Jetson can run the local loop. Cloud planner
integration follows only after this gate; the delay/drop flags below inject
**action-policy result delivery faults**, not cloud planning latency.

## Hardware qualification status (2026-09-29)

**The colocated learned-policy loop is not qualified.** On an 8 GB Orin,
JetPack/L4T 36.4.7, the current Dockerfile built successfully and passed
`pip check`, but its PyTorch 2.10.0+cu126 runtime failed a tiny CUDA reduction
with `no kernel image is available for execution on the device`. A matrix probe
also failed with `CUBLAS_STATUS_ALLOC_FAILED`. CUDA device discovery alone
incorrectly appeared healthy. The policy worker now checks an actual reduction,
matrix operation and NumPy conversion before loading weights.

The image used for the following measurements was
`sha256:de1e0c40688aa727d9ef4e446a5f80f995907ea6cd4a902578dc04a7ba1de6ba`.

| Trial | Result |
| --- | --- |
| Isolated physics/camera calibration | Physics p95 2.09 ms; camera p95 7.18 ms; NVIDIA Tegra Orin EGL renderer |
| Scripted controller, seeds 0/1/2, recording off | 3/3 benchmark successes and timing passes; per-episode physics-lag p95 0.090–0.099 ms |
| Same scripted controller, camera recording on | 3/3 benchmark successes, 0/3 timing passes; physics-lag p95 19.7–20.5 ms versus the 12.5 ms limit |
| Learned action policy | CUDA preflight fails; no learned-action timing or task-success result |

These are short smoke episodes (53–56 actions, about 0.7 seconds), not sustained
load qualification. The scripted controller has privileged state and is not a
learned model. Camera work in the physics process missed timing even without
learned inference, so it must be separated before qualifying the complete loop.
The no-recording result diagnoses that overhead; it is not a substitute for the
camera-enabled learned-policy experiment. No cloud-planner or delay/drop trial
has run. Keep this profile experimental until a supported Orin/Python/LeRobot
runtime and an independent camera loop pass the same timing criteria.

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
The Dockerfile uses the vendor image's graphics libraries but installs stable
PyTorch CUDA 12.6 wheels and experiment dependencies in an isolated virtual environment. The complete
resolved package list is saved inside the image at `/opt/experiment-packages.txt`.
The exact image ID and observed packages must accompany each qualification.
The initial vendor Torch build could execute a CUDA tensor but could not bridge
NumPy 2, and its Torchvision prerelease did not satisfy LeRobot's declared minimum.
The current recipe does not inherit those Python packages. Stable Torch/Torchvision
wheels are hash-pinned and `pip check` must succeed, but this is insufficient for
Orin GPU compatibility (see the failed hardware qualification above). This is a distinct
experimental runtime, not a reuse of the qualified CPU package lock.

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
