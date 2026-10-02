# Registered robot execution

This extends the existing deployment, worker, coordinator, mission, cancellation and execution
journal system to a robot's imported MuJoCo model. It does not replace the existing Sawyer paths.
The initial adapter accepts joint-position commands in SI units for 1–128 named actuated joints.
It executes functional lockstep simulation or measured, independently paced physics. Neither mode
commands physical motors. The physical robot and simulated instance retain the same immutable profile.

## User flow and current boundary

Open a project and click its simulated robot. The robot page shows simulator verification, available
application releases, the acknowledged deployment, Start/Stop, and persisted task outcomes. Start
requires the selected release to match the acknowledged deployment. Stop remains pending until the
coordinator acknowledges cancellation. Unknown execution prevents new tasks. Task history is queried
for that robot before the server applies its result limit.

Project → Configurations → Create runnable configuration now creates the application and first release
in one transaction. The existing Configurations index also lists project applications. The form pins
a registered profile, derives its joint order/limits/cadence and model identity, and accepts named
joint targets, success tolerances and execution timeouts. Choose the controlled reference or a trusted
operator-installed joint-state policy worker; entering a runtime/artifact name does not install it.

The release page preserves earlier releases, creates a new immutable release when edited, reports
matching verified simulators and opens their deployment controls with that exact release selected.
It offers exact serialized setup files: browser re-serialization would change numeric representations
and invalidate artifact identity. Saving a release never changes a running robot; deployment can trigger managed reference-worker preparation.

Existing workspace configuration documents remain readable/editable separately; their names are not
assumed to identify backend applications. An operator can explicitly link a saved specification to a
project application, with navigation in both directions and changed-source status. This association
does not translate declared model names into installed policies. Project connection setup is described
below. Task result summaries are available here. Uploaded visual replay for this joint interface is not
implemented yet. Local physics-state capture and
post-run image reconstruction are available as described below. Existing Sawyer recordings
keep their existing viewer.

## Project connection setup

Project → Robots → Add robot now creates a one-use enrollment token and command in the registration
form. Install this revision of `convoy-agent` on the intended computer first. Run the displayed command
there; the form polls enrollment and selects the newly enrolled connection. Then run its displayed
agent command to report heartbeats and hardware sensors. Simulator enrollment instead displays the
combined simulator service command described below. Choose a profile, optional fleet, and register.
The registration API remains the authority for assigning an unassigned caller-owned connection.

Tokens expire after 15 minutes and can be cancelled before use. The command is shell-quoted and each
token gets a separate relative data directory, avoiding another agent's existing identity. Run both
commands from the same working directory. The server stores the token hash; GET responses never recover
the plaintext command. Reloading the page loses the command, and an unused token expires naturally.
Consumed enrollment is not device revocation: cancellation returns a conflict once a device has claimed
it. The enrollment's project group is informational; project ownership is checked when creating setup
and registration enforces the final robot/project/profile/connection relationships.

Set `CONVOY_PUBLIC_URL` to the API origin reachable from the target computer before using this flow
across machines. Localhost is only appropriate for a same-host API and agent. A simulator enrollment
uses `--simulate --host-inventory`: it retains simulated-device identity but reports the actual host's
inventory, sensors and clock provenance, and does not start the older synthetic robot workload. Existing
`--simulate` demo behavior remains unchanged. The displayed hardware is agent-reported; unavailable
values remain unknown. This does not discover mechanics, calibrate dynamics or authorize physical motion.

API: `POST /api/v1/robot-connections/enrollments` creates setup for an owned `project_id`, `name` and
`simulated` flag; `GET /api/v1/robot-connections/enrollments/{id}` reports consumption;
`POST /api/v1/robot-connections/enrollments/{id}/cancel` cancels unused setup;
`GET /api/v1/robot-connections/{device-id}` returns caller-owned connection and bounded hardware details.
Creation is deliberately not replayed through a plaintext idempotency receipt. No new database schema.
Existing connections can still be selected. Physical-agent installation remains separate.

## Combined simulator service

From a checkout of the desired committed revision, install the runtime on the simulator computer:

```sh
bash integrations/simulation/install-runtime.sh "$HOME/.local/share/convoy/simulator-v1"
source "$HOME/.local/share/convoy/simulator-v1/runtime/bin/activate"
```

The installer snapshots committed source into a private installation directory, creates a Python 3.11
environment and installs the frozen `runtime` dependencies. It does not install the API server or test
tools. If `uv` is missing, it bootstraps a pinned version in its own environment using Python 3's venv.
It never changes the system Python, requests root or starts a background service. The initial install
needs package/Python download access; compatible cached dependencies can be reused. A different revision
needs a different destination. Retrying the same revision can resume dependency installation. The
checkout must have committed runtime changes, and the installed copy remains independent of later edits.

Run the enrollment command from Add robot, then its simulator command:

```sh
convoy-sim-service --data-dir ./convoy-connections/<enrollment-id>
```

The foreground service reports host health, waits for project registration, fulfills requested model
verification and watches for deployments/tasks. It embeds the existing connection agent on a thread and
uses the existing execution coordinator and managed worker. Inference and real-time physics retain their
separate processes. Native verification runs only when local execution/recovery work has settled. The
service rejects physical-device enrollments, unsupported engines and duplicate local owners. Ctrl-C or
SIGTERM requests coordinated shutdown. A successor recovers the execution journal and the proven owned
worker; it does not replay completed tasks. Local diagnostics are in `simulator-service/status.json`.

Normal automatic setup requires the API's existing `CONVOY_EXECUTION_SIGNING_KEYS_FILE` configuration
with separate action/planner Ed25519 keys. The service fetches **public action verification keys only**
from its authenticated enrolled API origin, before activation and between coordinator iterations. It
supports key rotation with stable issuer/audience; retain old public keys until their grants expire.
Changing trust anchors requires operator review of the installed trust file. Private signing keys stay
on the API; HMAC secrets are never downloaded. Explicit operator-configured verifier files or a local
test HMAC secret remain supported. A server without public signing configured reports unavailable setup,
while the connection agent continues reporting health. Nothing silently downgrades signing mode.

The service currently manages the joint reference worker. Operator-installed learned workers can still
use the standalone registered runner below. Camera-conditioned learned policies, cloud planner pairing,
Isaac execution, system-service installation and a browser-downloadable runtime distribution remain
unfinished. Installing this runtime does not calibrate the robot's mechanical model or command motors.

## Robot model delivery

Project → Profiles → Simulation files accepts the MJCF/ZIP model already pinned in that profile's
SHA-256. The browser checks the fingerprint for immediate feedback; the API independently verifies
it before publishing the file. A different model requires a new immutable profile revision. The
upload is byte storage, not simulator verification or physical calibration.

The qualification runner and registered execution runner fetch missing or damaged cached models from
their enrolled control plane. Only a simulated device assigned to that exact profile and engine may
download it. They never fetch the arbitrary `asset.uri` from a profile or a model response. Downloads
are bounded, reject redirects, validate the digest and atomically publish before native model loading.
Owner-provisioned local assets continue to work without an upload. A missing upload or failed transfer
is a failed verification with a retry action, not fabricated simulator readiness.

Uploads are at most 16 MiB. The API streams to a temporary file under an exclusive store lock, limits
stored robot assets to 1 GiB by default (`CONVOY_ROBOT_ASSET_QUOTA_BYTES`), and rechecks the caller before
publication. Identical retries do not consume another stored copy. Current server files live at
`CONVOY_DATA_DIR/artifacts/robot-models/<profile-id>/<sha256>` on the existing persistent data volume.
Back up this directory with the data volume; a database-only backup does not include model bytes.
Filesystem-backed delivery requires that volume; the separate CPU-only AWS staging allowlist does
not expose these routes. Larger bundles, storage management and external object storage remain future
work. Initial runner enrollment, trust configuration and launch still require setup.

API: `GET /api/v1/robot-profiles/{id}/simulation-assets` reports storage availability;
`POST /api/v1/robot-profiles/{id}/simulation-assets/{engine}` receives `application/octet-stream`;
`GET /api/agent/v1/robot-assets/{profile-id}/{engine}` delivers to its assigned simulator. Uploads are
content-addressed retries and need no separate idempotency key. No database migration is required.

## Release contract

The UI uses `POST /api/v1/configurations` with `name`, `project_id` and a `configuration` containing
`profile_id`, named `targets`, `instruction`, `policy`, optional success tolerances and execution limits.
`policy` is either `{"kind":"reference"}` or `{"kind":"installed","runtime":"…","artifact_sha256":"…"}`.
The response contains the authoritative `application` and `release`. New compiled revisions use
`POST /api/v1/applications/{id}/configuration-releases`. All mutations require the existing idempotency
header and operator role, with profile/application project ownership enforced by the API.

`GET /api/v1/applications/{id}/releases/{release_id}/setup` returns `manifest_json` and, for matching
reference-policy releases, `reference_policy_json` as strings preserving canonical numeric bytes.
Save these strings directly rather than parsing and serializing them in JavaScript.

Advanced callers can still use `POST /api/v1/applications` and
`POST /api/v1/applications/{id}/releases` directly. The release's `manifest` has this shape (replace digests and robot fields with the actual registered
profile and installed artifacts):

```json
{
  "schema_version": 3,
  "profile": "registered-joint-policy-v1",
  "policy": {"runtime": "convoy-joint-target-reference-v1", "artifact_sha256": "<canonical reference-policy digest>"},
  "environment": {
    "engine": "mujoco", "version": "3.3.0",
    "robot_profile_sha256": "<registered profile digest>",
    "asset_sha256": "<installed model digest>"
  },
  "interface": {
    "joint_names": ["shoulder"], "command_interface": "joint-position",
    "action_bounds": [[-1, 1]], "control_rate_hz": 50
  },
  "task": {
    "instruction": "Reach the shoulder target", "target_joint_positions": [0.25],
    "position_tolerance": 0.01, "velocity_tolerance": 0.02
  },
  "execution": {"max_steps": 200, "decision_timeout_ms": 1000, "mission_timeout_s": 60}
}
```

The server compares the model, profile digest, joint order, bounds and cadence against the registered
profile. Verification must still be current at deployment, ready acknowledgment, task creation and
claim. An earlier registry-only `custom-unqualified` record with an implicit joint-position execution
profile can be upgraded by an explicitly requested matching deployment; unrelated profiles cannot.
Ordinary ownership, generation, evaluation reservation, promotion and unresolved-task rules still apply.

Observations contain ordered `positions`, `velocities` and `simulation_time_s`. Actions contain one
position per declared joint and must satisfy the declared bounds. This adapter exposes joint state,
not rendered images, to the policy. Task success requires every joint to reach its target within the
position tolerance and settle below the velocity tolerance. The initial state is the pinned model's
default state; different seed labels do not imply randomized scenarios in this adapter.

## Runtime

Use the existing `convoy-worker` with a trusted installed runtime factory. The runtime declares the
registered profile, reports its artifact identity, implements `reset_session(identity)`, and returns
joint positions from `get_action(observation)`. The worker and coordinator both validate action
shape/bounds. Worker sessions fence repeated observations and reset per mission. Each worker reports
a process incarnation; a restart creates a new locally observed deployment binding.

The included `convoy_sim.joint_reference:from_file` is a controlled reference, **not a learned model**.
Its JSON artifact is `{"target_joint_positions": [0.25]}`. Its artifact identity is the contracts
package's `canonical_digest` of that document. Set `CONVOY_JOINT_REFERENCE_FILE` to the artifact path.
Configure the worker's existing action verification keys (or explicit local HMAC test setup) and
`CONVOY_WORKER_PROBE_TOKEN`; never put signing keys in the coordinator. From `integrations/simulation`:

```sh
uv sync --frozen --extra managed
uv run --frozen --extra managed python -m convoy_worker.cli \
  --release /path/to/release-manifest.json --factory convoy_sim.joint_reference:from_file

uv run --frozen --extra managed python -m convoy_sim.registered \
  --data-dir /path/to/simulator-enrollment --assets /path/to/robot-assets \
  --worker-url http://127.0.0.1:8091
```

For reference-policy releases, the runner can now own its policy worker. With the same enrolled
simulator, uploaded (or locally provisioned) robot assets and action verification configuration, run:

```sh
uv run --frozen --extra managed python -m convoy_sim.registered \
  --data-dir /path/to/simulator-enrollment --assets /path/to/robot-assets --manage-worker
```

No policy file, per-release worker command or probe token needs to be assembled in this mode.
The runner derives the reference artifact from the validated release, writes private canonical setup
files, launches a fixed installed worker and verifies its exact release/runtime/artifact before
acknowledging readiness. It switches the owned worker only behind the coordinator's idle/recovery
fences. Failed starts are retained across runner restarts and require a new deployment generation to
retry. An unsupported installed-policy release is blocked before stopping the previous worker.
Shutdown verifies the owned child's exit; bounded logs and process records remain for diagnosis.

`--manage-worker` and `--worker-url` are mutually exclusive. Other learned policy runtimes still use
an operator-managed external worker. Initial enrollment/trust configuration and simulator verification
still require setup; this is automatic reference-worker preparation, not
universal model installation. No cloud planner or physical motor controller is launched by this mode.

The coordinator uses its enrolled device identity and a dedicated journal. Its bundle owner reloads
and hashes the installed model, validates profile compatibility and native model readiness, and probes
the worker before acknowledging a deployment. The adapter captures the verified bytes; changing a
file cannot replace the model underneath an admitted task. The worker endpoint is configured by the
operator, not taken from a model response. Remote endpoints require HTTPS and can use
`--worker-ca-file`. SIGINT/SIGTERM use the existing coordinator stop/reconciliation path.

## Evidence and remaining work

### Independent physics and timing

Select **Measured real time · physics advances independently** in the configuration form to pin observation freshness and
physics lag limits. Omitting `execution.timing` retains the existing lockstep behavior and digest.
The optional execution field is:

```json
"timing": {
  "mode": "realtime",
  "max_observation_age_ms": 200,
  "max_physics_lag_ms": 20,
  "fallback": "hold-position"
}
```

This mode requires at least 10 Hz. `max_steps` bounds physics control ticks, rather than the number
of policy requests; native MuJoCo substeps still use the imported model's timestep. A separate owned
process advances physics against the host monotonic clock while the coordinator waits for inference.
Inference starts from a timestamped observation. The request deadline is the earliest of task expiry,
decision timeout and observation expiry; the physics process checks it again before applying an action.
Expired commands are durably reported as not applied. Unknown admission or cleanup prevents blind retry.

Until the first action arrives, and after an applied target expires, the simulation holds bounded joint
positions through its existing position controller. This is simulated fallback behavior, not a physical
emergency stop. Excess physics dispatch/completion lag ends the run with failed timing evidence, including
a stall detected during cleanup. Physics is not silently slowed to accommodate inference.

The task result separates task success from timing status and displays distributions for policy wait,
observation-to-result, observation-to-applied-action, physics dispatch lag and completion lag. Policy
wait includes transport and queueing; it is not an isolated inference-time measurement. All intervals
are observed on one host; no remote worker clock subtraction is used. Physics ticks, policy actions,
fallback use and physics wall duration are recorded separately. Fewer than 200 physics ticks or 10
applied actions is insufficient evidence even if the task succeeds; these minimum sample counts are
only a reporting floor, not statistical certification or a guarantee of future timing.

Deadline failure, missing responses, physics lag and expired targets between results report timing
failure. Sustained runs can report `observed_deadlines_met` independently of task success. Existing
offline evaluation promotion gates require lockstep evidence and do not accept these timing results.
A dedicated repeated timing-evaluation workflow remains to be implemented.

Native tests exercise fresh commands, stale action rejection while physics advances, target expiry,
an intentionally paused physics process, and sustained timely actions that do not complete the task.
The HTTP pipeline covers a fast managed worker and a deliberately delayed external worker using the
same registered robot/model contract. Browser tests cover creating/revising the timing contract and
reading results on desktop and mobile. Native Jetson evidence and its limits are recorded in
[registered-timing-jetson.md](registered-timing-jetson.md) and [platform-build.md](platform-build.md).

## Physics-state recording and local replay reconstruction

The registered CLI and combined simulator service now capture bounded physics trajectories in
`<enrollment-data-dir>/trajectories/<mission-id>.state.json`. Each includes the execution identity,
exact release manifest, model-state widths and an initial state followed by every completed control
tick. Samples contain MuJoCo position/velocity/actuation/control/mocap state, simulation time, host
monotonic capture time and the applied target. They distinguish a new policy command, a held policy
target and fallback hold. Physics substeps between control ticks are not separately recorded.

Independent real-time recording happens inside the physics process, so inference delays do not leave
gaps where the robot continued moving. Its copy overhead is included in measured physics completion
lag. No rendering, PNG compression or disk writes occur in the control loop. After acknowledged physics
shutdown, the coordinator writes the immutable trajectory and reports its SHA-256 under
`episode.summary.recording`. Artifact export time is outside the reported execution duration. A failed
export reports recording unavailability without changing task success or causing another action.

Limits: 501 samples, 2,048 model-state values per sample, 16 MiB per trajectory and 256 MiB in the local
trajectory directory. A limit failure is explicit; there is no silent downsampling or automatic deletion
of earlier evidence. Hosted upload/retention management is a subsequent integration.

From the installed runtime, reconstruct frames with the digest reported by that task:

```sh
convoy-sim-render \
  --trace /path/to/enrollment/trajectories/mission-id.state.json \
  --sha256 <digest-from-task-result> \
  --asset /path/to/robot-assets/<pinned-model-sha256> \
  --asset-format mjcf \
  --output /path/to/new-replay-directory
```

For a ZIP model, use `--asset-format bundle`. Rendering runs in a bounded native subprocess, validates
trajectory/release/model identity and engine version, restores the recorded states, then runs
`mj_forward` to reconstruct each pose. It never steps physics or invokes a policy. Output is a sequence
of 320×320 PNGs and a final `index.json` containing frame hashes, action provenance, times, joint labels
and observer-camera settings. A directory without that final index is incomplete; existing output
directories are not overwritten. Rendering requires a functioning MuJoCo OpenGL backend; the Jetson
acceptance uses `MUJOCO_GL=egl`, and CI uses software OSMesa.

These images are explicitly observer reconstructions, **not policy camera observations**. Local rendering
does not make the recording available in the hosted player yet. Automatic upload, API validation and
the registered-robot result/player connection remain necessary before that user journey is complete.

### Scope

The managed acceptance uses real HTTP APIs, enrollment, a real worker, a coordinator subprocess and
native MuJoCo. It verifies successful movement, cancellation during deliberately slow inference, and
an out-of-range policy action that fails before a control step. An empty or damaged local asset cache is
restored from the uploaded model; corrupt source bytes fail verification without publishing a cached file.
Server tests also reject incompatible assets/interfaces and a claim
whose verification was superseded. Existing legacy coordinator, worker/session and lifecycle tests
continue to run, including PostgreSQL cases and browser start/stop acknowledgment checks.

Remaining: learned policies for registered joint interfaces; camera-conditioned contracts; model
installation; cloud planning and chat tasking; repeated timing evaluations of learned workloads;
uploaded visual replay for these models; other controller adapters; Isaac, scenarios, fleet rollout,
and dynamics characterization. Reference-controller measurements do not establish learned manipulation
quality or calibrated physical fidelity.
