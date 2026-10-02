# Registered robot execution

This extends the existing deployment, worker, coordinator, mission, cancellation and execution
journal system to a robot's imported MuJoCo model. It does not replace the existing Sawyer paths.
The initial adapter accepts joint-position commands in SI units for 1–128 named actuated joints.
It executes functional lockstep simulation; it does not qualify real-time behavior or command
physical motors. The physical robot and simulated instance retain the same immutable profile.

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
assumed to identify backend applications. Explicit linking/migration of those documents, automatic model
installation and project onboarding remain unfinished. Task result summaries are available here;
uploaded visual replay for this joint interface is not implemented yet. Existing Sawyer recordings
keep their existing viewer.

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
simulator, installed robot assets and action verification configuration, run:

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
an operator-managed external worker. Robot asset delivery, initial enrollment/trust configuration and
simulator verification still require setup; this is automatic reference-worker preparation, not
universal model installation. No cloud planner or physical motor controller is launched by this mode.

The coordinator uses its enrolled device identity and a dedicated journal. Its bundle owner reloads
and hashes the installed model, validates profile compatibility and native model readiness, and probes
the worker before acknowledging a deployment. The adapter captures the verified bytes; changing a
file cannot replace the model underneath an admitted task. The worker endpoint is configured by the
operator, not taken from a model response. Remote endpoints require HTTPS and can use
`--worker-ca-file`. SIGINT/SIGTERM use the existing coordinator stop/reconciliation path.

## Evidence and remaining work

The managed acceptance uses real HTTP APIs, enrollment, a real worker, a coordinator subprocess and
native MuJoCo. It verifies successful movement, cancellation during deliberately slow inference, and
an out-of-range policy action that fails before a control step. Simulator asset corruption still
produces a failed verification. Server tests also reject incompatible assets/interfaces and a claim
whose verification was superseded. Existing legacy coordinator, worker/session and lifecycle tests
continue to run, including PostgreSQL cases and browser start/stop acknowledgment checks.

Remaining: learned policies for registered joint interfaces; camera-conditioned contracts; model
installation and configuration revision links; cloud planning and chat tasking; independent real-time
physics/timing; visual replay for these models; other controller adapters; Isaac, scenarios, fleet
rollout, and dynamics characterization. Do not label this reference-controller result as evidence of
learned manipulation, calibrated physical fidelity, or Jetson timing performance.
