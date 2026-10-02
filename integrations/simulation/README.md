# Action-policy simulation reference

This optional package runs a real MuJoCo physics environment through MetaWorld:
a Sawyer robot arm manipulating an object in `pick-place-v3`. It records the
observation → policy → action → physics → outcome loop. It does not install ROS,
PyTorch, CUDA, or simulator dependencies into Convoy's server or device agent.

The supplied policy is MetaWorld's **scripted reference**, not a learned model.
The `zero` policy provides a deliberately unsuccessful comparison. A Python
policy implementing `get_action(observation)` can be passed to the runner for
experiments; serving a pinned learned checkpoint is the next integration slice.

## Run

From this directory, using Python 3.11 and uv:

```sh
uv sync --frozen
uv run --frozen convoy-sim --episodes 3 --seed 0 --require-success --output runs/reference
uv run --frozen convoy-sim --policy zero --episodes 3 --seed 0 --output runs/zero
```

Each command requires a new output directory, so a rerun cannot silently replace
previous evidence. Local `runs/` is ignored by Git. No GPU, model download, or
cloud account is required. Headless physics runs on macOS and Linux. The lockfile
pins the complete Python dependency set; the reference environment, controller,
and assets come from the pinned MetaWorld distribution.

To record a video:

```sh
uv sync --frozen --extra video
uv run --frozen --extra video convoy-sim --episodes 1 --video --output runs/video
```

macOS uses the default renderer. On a headless Linux machine, install OSMesa and
set `MUJOCO_GL=osmesa` for CPU rendering, or use a compatible EGL/GPU environment.
Ordinary CI does not initialize a renderer. Video is sampled every fourth control
step and played at simulated speed; it is not a recording of wall-clock timing.

## Contract and evidence

- Observation: 39 values, including privileged object state and the target.
  This is not a camera-based perception or VLA benchmark.
- Action: four normalized values in `[-1, 1]`: XYZ motion and gripper command.
  The pinned environment defines the physical scaling and controller semantics.
  The reference expert's unconstrained outputs are explicitly clipped by its
  adapter; other policies are validated and rejected for invalid shape, nonfinite
  values, or out-of-range commands.
- Time: the pinned environment advances 0.0125 simulated seconds per action.
  The benchmark horizon is 500 actions. Shorter diagnostic runs are allowed.
- Success: report MetaWorld's object-to-target success signal at the final step
  and whether success occurred at any step. The target can be above the table;
  this is not proof of releasing an object onto a surface.
- Timing mode: **offline lockstep**. Physics waits for policy inference. Recorded
  wall time and policy-call duration do not establish real-time network tolerance.

Each run contains:

```text
manifest.json         configuration, package versions, source revision, evidence scope
summary.json          completion/errors and per-episode task outcomes
episode-<seed>.jsonl  initial state, each action, resulting state, and outcome
episode-<seed>.mp4    optional rendered recording
```

`completed` means the experiment ran; inspect `final_success_rate` for task
performance. Errors and missing episodes remain in the requested denominator.
`--require-success` returns exit code 1 when any completed episode fails its task;
runtime failures return 2. Policy exceptions are retained in the evidence. The
initial state's hash and action/observation trace support reproduction within
the pinned environment; do not assume bit-identical physics across platforms.

MetaWorld currently emits observation-space and scripted-gain warnings for this
task. They remain visible. Convoy checks finite observations and action validity;
it does not infer safety or benchmark correctness merely from a successful run.

## Development checks

```sh
uv run --frozen ruff check src tests
uv run --frozen pytest -q
```

The focused suite runs the scripted and zero policies through actual physics,
checks same-host seeded repeatability and evidence linkage, rejects malformed
actions, records a failed policy, and protects prior output. The workflow also
executes the installed CLI and uploads a small evidence artifact. There are no
mocked assertions that a simulator method was called and no broad UI snapshots.

## Next slices

1. Bind a pinned learned checkpoint to its exact observation/action transforms.
   LeRobot/LIBERO is a candidate for visual policies on Linux; it is a different
   contract from this state-based MetaWorld reference.
2. Separate policy execution into a local/remote worker, keeping physics advancing
   under an explicit action-buffer/fallback policy when inference is late.
3. Register these runs as Convoy evaluation jobs and compare releases in the UI.

This package is an executable foundation for those changes, not the completed
mission/deployment platform. No weights are trained, physical robot controlled,
or cloud resources provisioned by the runner.

Sources and licenses: [MetaWorld](https://github.com/Farama-Foundation/Metaworld)
and its [MIT license](https://github.com/Farama-Foundation/Metaworld/blob/main/LICENSE),
[expert policies](https://metaworld.farama.org/benchmark/expert_trajectories/),
[MuJoCo](https://github.com/google-deepmind/mujoco), and
[LeRobot's LIBERO integration](https://huggingface.co/docs/lerobot/libero).

## Import episodes recorded elsewhere

`scripts/import_offline_eval.py` (standard library only) uploads episodes from any simulator, in
the replay format, as an unsigned offline evaluation that Configurations shows as "Offline sim":

```sh
python scripts/import_offline_eval.py --write-example /tmp/offline-example   # generic frames
CONVOY_SERVER=https://deployconvoy.com CONVOY_EMAIL=… CONVOY_PASSWORD=… \
  python scripts/import_offline_eval.py /tmp/offline-example
```

The layout, limits and API are in [offline evaluations](../../docs/v1/offline-evaluations.md).
`CONVOY_API_TOKEN` with an API origin replaces the sign-in. Credentials are never printed.

## Run through the Convoy deployment and mission pipeline

The optional `managed` extra connects this simulator to the real control-plane
API and a separate action-policy worker. See the [managed manipulation
reference](../../examples/manipulation/README.md) for the reproducible three-process
acceptance run, including cancellation and restart recovery. Its baseline is
still scripted and runs in offline lockstep.

## Bimanual pill task

`convoy_sim.bimanual_pill_task` (CLI `convoy-sim-pills`) is a second, separate
scenario: a wheeled bimanual station robot (two 6-DOF arms with parallel
grippers) putting 20–30 capsule pills into an open bottle, with five
planner/policy deployment configurations (Edge device planner, Cloud GPT-6 Luna
(vision), Edge Qwen + GPT Astra, GPT Astra, Edge SmolVLA + GPT Astra), seeds ×
slices evaluation, and recordings in the offline import format above (or the
hosted journal format). Its skills read simulator state, except the vision
planner's pick, which goes to a point the model chose in the camera image.

- **Edge device planner.** Its decisions are real calls to the model of a
  connected device's active release, through the website's device chat contract
  (`platform-chat-v1`): a text description of the scene in, one JSON action out,
  latency measured, and the model and transport recorded with the run.
- **Cloud GPT-6 Luna (vision).** Its decisions are real calls to `gpt-6-luna`
  (OpenAI Responses API, low effort) with the head camera image: it points at the
  next pill in pixels. The pixel is back-projected with the camera's depth and a
  scripted IK grasp goes there. Every call's cost goes to a capped spend ledger.
- **The other three** use a deterministic stand-in and differ by modeled planner
  latency and outage behaviour. SmolVLA, which has no checkpoint for this robot,
  places no pills.

See [docs/bimanual-pill-task.md](docs/bimanual-pill-task.md) for the model, the
physics note, the on-device and vision planners, metrics, results and the format
mappings.

```sh
uv sync --frozen --extra video --extra managed
uv run --frozen pytest -q tests/test_bimanual_pill_task.py tests/test_device_planner.py tests/test_vision_planner.py
uv run --frozen convoy-sim-pills episode --config cloud_astra_only --slice nominal --seed 0 --output runs/pills
scripts/pill_task_eval.sh runs/pill-demo     # modelled matrix, one importable directory per configuration
```

`scripts/render_robot_preview.py` renders the robot alone in its idle pose as a turntable loop
(VP9 and H.264) and a still, for the website's configuration page; it writes
`website/public/sim/bimanual-station/` (needs OpenGL, e.g. `MUJOCO_GL=egl`, and `--extra video`).
