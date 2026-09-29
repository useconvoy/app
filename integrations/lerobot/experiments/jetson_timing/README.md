# Jetson timing qualification

This opt-in experiment measures whether a specific policy, simulator, camera and
execution schedule fit a timing contract on the same edge host. It does not
modify the production agent, the qualified CPU runtime, managed deployment gates
or existing replay journal. No physical actuation interface is present.

## What is measured

Three processes share one host monotonic clock:

1. **Physics:** MetaWorld pick-place at the original 80 Hz / 12.5 ms action cadence.
2. **Camera:** render an immutable physics-state snapshot in a separate MuJoCo
   scene. Send pixels directly to inference, never through the physics process.
3. **Policy:** run the verified SmolVLA weights, return a timestamped action chunk.

There is at most one observation request in flight. Physics continues while
camera/inference are busy. The observation timestamp refers to when the source
physics state was produced, not when rendering finished. `mj_forward` canonicalizes
kinematics before snapshotting; its cost is included in the control loop. The
camera and four proprioceptive values describe that same state. Initial state
hashes and an explicit camera-replica check protect scene consistency.

Absolute tick deadlines are never reset to hide drift. Action times are anchored
to their source observation; expired prefixes are skipped. A new chunk replaces
valid future actions. This is simple timestamped playback, **not RTC blending**.
An empty buffer yields zero normalized Cartesian delta and the last gripper
command. That fallback is specific to this simulation, not a certified physical
stop or a claim of grasp preservation.

Every result records camera queue/render time, inference queue/compute time,
delivery time, observation-to-result latency, observation-to-action age, dispatch
and completion lateness, stale/dropped results, startup fallback and steady
fallback. Results still in flight when the control window ends are marked and
excluded from successful-delivery counts.

## Timing contract and discovery

The default experimental contract is explicit and adjustable:

- Physics dispatch and completion p99 lateness at most one 12.5 ms period.
- Source observations and action horizon expire after 625 ms (50 trained actions).
- At most 5% steady-state fallback ticks; startup fallback is reported separately.
- At least 200 control steps and 10 completed learned-policy results per episode.
- A 250 ms simulator overrun aborts the trial. No silent slow simulation.

The result is `failed`, `insufficient_evidence`, or `passed_observed_contract`.
Task success is separate. A pass describes those observations, not a hard real-time
guarantee or a deployment approval. These short trials do not qualify thermal soak,
rare latency tails, other model architectures, wireless handoffs or physical robots.

The report estimates the buffer span needed for continuous supply as
**p99 observation-to-result latency + p99 result interval + one control period**.
This captures a key failure: a chunk may arrive before its expiry yet leave too
little future coverage to last until the next result. The estimate is not a
permission to extend the checkpoint's trained horizon, slow its action cadence,
or relax freshness requirements. If the required span exceeds the valid horizon,
optimize the pipeline, use suitable hardware or choose a different policy.

`--sweep` runs a baseline and then bounded delay/drop cases. It stops if baseline
qualification fails, rather than spending time testing additional latency on an
already unsuitable configuration. These are **local action-policy delivery
faults**, not measurements of cloud planning or Wi-Fi. Remote planner experiments
follow a viable local action loop.

## Runtime and reproducibility

The Dockerfile pins the NVIDIA L4T JetPack CUDA 12.6 base and Jetson-specific
PyTorch 2.8 / Torchvision 0.23 wheels. LeRobot 0.4.4 supports that Python 3.10
runtime and the saved SmolVLA processors. The application CPU profile remains
LeRobot 0.6.1; this experiment has a different identity. Assets are checksum-verified,
weights load strictly, compilation is disabled, and a real GPU reduction, matrix
operation and buffer conversion run before model loading. No CPU fallback is hidden.

Default weights/compute are float32. `--precision bfloat16` or `float16` explicitly
converts weights on CPU before GPU loading and enables autocast; each is a
separate measured configuration. Unused warmup allocations are released before
camera initialization, not flushed during the control loop.
The manifest saves package versions, compute precision, source hashes, image ID,
GPU identity, initialization/warmup time and settings. Full package resolution is
also saved in the image at `/opt/experiment-packages.txt`.

The Jetson Torch wheel was compiled against NumPy 1, while LeRobot dependencies
require NumPy 2. This adapter uses Torch’s public Python buffer interface for RGB,
plain lists for proprioception/actions, and supplies the same saved normalization
statistics through the public processor override API. Their tensor values are
checked against the checkpoint before readiness. The adapter never calls
`from_numpy` or `.numpy()`.
The import-time NumPy-ABI warning remains visible in logs; the C bridge is not
claimed to work. Model warmup checks the complete processor/inference path, so a
future dependency that reintroduces C-bridge calls fails readiness.
Action postprocessing is batched; warmup checks bit-for-bit equality against the
checkpoint's per-row postprocessing before accepting requests.

On the tested Orin Nano, EGL rendering works alone but failed when co-loaded with
the model. The working combined configuration uses CPU OSMesa rendering with CUDA
policy inference and bfloat16 weights. This is a measured fallback, not a fix for
GPU-renderer co-loading. The commands below explicitly select that configuration.

Earlier attempts are retained as failure evidence: generic ARM CUDA wheels
discovered Orin but lacked usable GPU kernels.
`pip check` and `cuda.is_available()` alone were insufficient qualification.

```sh
docker build -t convoy-jetson-timing:jp6 integrations/lerobot/experiments/jetson_timing
export CONVOY_TIMING_ROOT=/home/jetsy/convoy-experiments/jetson-timing
export CONVOY_TIMING_GL=osmesa
bash "$CONVOY_TIMING_ROOT/repo/integrations/lerobot/experiments/jetson_timing/run-on-jetson.sh" \
  camera-check --mode verify-camera --seeds 0
bash "$CONVOY_TIMING_ROOT/repo/integrations/lerobot/experiments/jetson_timing/run-on-jetson.sh" \
  scripted-baseline --mode realtime --policy scripted --seeds 0,1,2 --sustain --record
bash "$CONVOY_TIMING_ROOT/repo/integrations/lerobot/experiments/jetson_timing/run-on-jetson.sh" \
  learned-reference --mode lockstep --policy smolvla --precision bfloat16 --chunk-size 1 --seeds 0
bash "$CONVOY_TIMING_ROOT/repo/integrations/lerobot/experiments/jetson_timing/run-on-jetson.sh" \
  learned-timing --sweep --precision bfloat16 --seeds 0,1,2 --record
```

Run names must be new. The launcher mounts selected experiment sources and public
assets read-only, a fresh results directory read-write, and no production state.
It disables networking and caps CPU/memory. On a shared-memory GPU, the container
memory cap is not a guarantee about every driver allocation. Keep other inference
workloads and the host power mode documented when comparing runs.

Camera size, model image size, denoising steps, shadow-map size and MSAA are explicit
configuration options, not adaptive changes inside a trial. Faster settings can
change the observations and policy quality; repeat both task and timing checks.

## Measured Orin results (2026-09-29)

Jetson Orin Nano 8 GB, MAXN_SUPER mode, with the existing Convoy agent/model left
running. The container was capped at four CPUs and 4.5 GB of memory. These are short
qualification trials, not a statistical reliability claim.

| Configuration | Task result | Timing finding |
| --- | --- | --- |
| Scripted control + independent OSMesa camera, three seeds × 500 steps | 3/3 successful | Met the observed physics timing contract; privileged-state controller, not learned control |
| SmolVLA bfloat16, 480px camera / 512px model images / 10 denoising steps, offline | 1/1 successful, 56 actions | 88.62 s wall time for 0.70 s simulated time; proves inference/task execution only |
| Same learned configuration, wall-clock playback, three seeds × 500 steps | 0/3 successful | All 9 returned chunks stale; worst-episode observation-to-result p99 1.647 s versus 0.625 s validity; physics completion lateness p99 0 ms |
| 256px camera / 256px model images / 5 denoising steps, reduced shadows/no MSAA, offline | Failed to finish in the 90 s budget (131 actions) | Median observation-to-result 0.688 s, p99 0.721 s; faster but still beyond validity and task quality did not qualify |

The wall-clock learned baseline used 100% fallback and yielded only three results
per episode. This is enough to demonstrate the observed deadline failures, not to
estimate rare tails or establish a passing configuration. Its rough continuity
estimate is 264 actions, beyond the checkpoint's 50-action horizon; do not extend
the horizon to manufacture a pass. The sweep stopped at baseline failure, so no
added-delay/drop tolerance is claimed.

The measured bottleneck is camera + inference, not physics or cloud networking:
worst-episode p99 camera 654 ms and inference 999 ms (individual percentiles are
not additive). Next, qualify a faster camera/inference path or a more suitable
policy/device while preserving task success, then run the fault sweep. Cloud
planner latency is a separate experiment after viable local execution.

`lockstep` waits for each newly inferred action and is an offline correctness
reference, never a timing pass. Scripted control reads privileged simulator state.
`--sustain` continues after first benchmark success to the bounded 500-step horizon;
success is latched but is not a claim that the goal remains stable throughout.

`--record` keeps at most 120 frames in camera-process memory and writes PNGs after
the timed loop. `frames.json` records actual source timestamps. Do not play irregular
captures at a fabricated uniform cadence. Episodes retain per-action JSONL traces
and result JSON even on failure. The sweep produces `matrix.json`, `report.json`
and `report.html`. To compare independent runs locally:

```sh
PYTHONPATH=integrations/lerobot/experiments python -m jetson_timing.report \
  --output timing-report.html /path/to/run-a /path/to/run-b
```

## Focused validation

Use Python 3.10 or later:

```sh
PYTHONPATH=integrations/lerobot/experiments python -m unittest discover \
  -s integrations/lerobot/experiments -p 'test_*.py'
```

Tests cover expiry/out-of-order playback, invalid actions, starvation despite low
latency, startup versus steady fallback, late physics dispatch and insufficient
samples. Hardware checks verify copied scene/proprioception, camera rendering,
CUDA and actual learned execution; no checkpoint downloads run on every PR.
