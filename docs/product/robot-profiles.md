# Robot profiles and simulation lineage

A project owns independent robots, immutable robot profile revisions and fleets. A registered
physical robot can have one or more simulated instances; each uses its own enrolled simulator
device and points to the physical robot's exact immutable profile revision. A new profile revision
does not silently change an existing robot or invalidate the evidence recorded for the old one.

## UI

`/app` opens Projects. Create/open a project, import a profile in Profiles, then register a robot
in Robots. Select an enrolled physical computer or simulator runner. For a physical robot with
simulation assets, **Create simulated instance** pins the source robot and profile; select the
simulator and its separate connection. Fleets can be selected during registration or assigned to
selected robots afterwards. Existing Configurations and application/episode URLs remain reachable.

The first registry slice does not yet install an agent, run imported models, or authorize motion.
A declared simulation asset is labelled **assets-declared**, not executable or physically validated.
The simulator verification flow below loads and hashes the selected asset and checks its interfaces.
Verified joint-position registrations can execute matching releases through the
[registered execution adapter](registered-execution.md). Configuration editing and automatic
installation still need integration. Existing qualified simulator records retain their execution path.
This is a staged implementation, not the final workflow.

## Profile import

The browser accepts a JSON specification up to 128 KiB. It contains:

- `schema_version: 1`, `embodiment`, optional description and pinned `description_asset`.
- `joints`: named revolute/prismatic/continuous/fixed joints and optional limits; paired bounds must
  be ordered. Units are SI: radians, metres, seconds, Newtons/Newton-metres. Joint offsets, axis
  topology, link geometry and inertia live in the pinned robot-description asset.
- `sensors`: names, kind, reference frames and optional sampling rates.
- `command_interface`: skill, Cartesian pose, joint position, velocity or torque.
- `adapter`, `control_rate_hz`: declared controller interface and cadence.
- `dynamics`: provenance (`manufacturer`, `imported`, `measured`, `estimated`, `unknown`) and note.
  These are user declarations; they are not independent measurements or qualification results.
- `simulations`: at most one MuJoCo and one Isaac entry, each with an engine version, controller,
  evidence and a pinned asset (`uri`, SHA-256 and format). The API does not fetch or execute assets.
- Optional `execution_profile`: association with an existing runner contract. Merely naming a known
  profile does not bypass asset qualification.

`/robot-profile-template.json` is a starting template with missing simulation assets, not a working
robot. Replace its placeholders and cadence with actual specifications. Example simulation entry:

```json
{
  "engine": "mujoco",
  "engine_version": "3.3.0",
  "controller": "position",
  "asset": {
    "uri": "artifact:your-uploaded-robot-bundle",
    "sha256": "<actual 64-character lowercase SHA-256>",
    "format": "bundle"
  },
  "evidence": {"source": "imported", "note": "Imported manufacturer model; calibration pending"}
}
```

## API and storage

All mutations use the existing operator role, CSRF/session boundary, audit and durable idempotency
receipts. Reads and writes check project ownership, including administrator callers. New robot
registration requires a device enrolled by the caller. The onboarding connection list contains only
unassigned, unretired, non-revoked devices enrolled by that caller. Cross-project profiles, source robots and fleet memberships are rejected.

- `GET /api/v1/robot-connections` lists the caller’s available enrolled devices.
- `POST/GET /api/v1/robot-profiles`, GET requires `project_id`.
- `GET /api/v1/robot-profiles/{id}`.
- `POST /api/v1/robot-registrations` and `GET /api/v1/robots/{id}`.
- `POST/GET /api/v1/fleets`, GET requires `project_id`.
- `POST /api/v1/fleets/{id}/members` accepts a batch with each robot's `expected_fleet_id` (or null).
- `POST /api/v1/fleets/{id}/members/{robot_id}/remove` removes membership, never the robot.

Profile creation uses project/name plus `expected_revision` (0 for a new profile). A stale revision
or stale fleet assignment fails with 409. The entire fleet assignment batch is transactional.

SQLite adds four new tables using the existing schema-5 additive migration mechanism; existing
robot, release, episode and document records remain intact. PostgreSQL adds frozen migration
`0005_robot_registry`; stop API/worker and run the existing migration command before its new release.
Legacy frontend document robot records are not automatically guessed or merged by display name;
the configuration-linking slice must reconcile explicit backend identities.

## Simulator verification

In Project → Robots, a registered MuJoCo instance offers **Verify simulator**. The website requests
a device-bound check and polls its result every five seconds while visible. States distinguish a
pending request, passed/failed checks, a ten-minute request expiry, and a stale device binding.
Isaac assets can be declared but are explicitly unverified; this slice has no Isaac runner.

The owner must provision the pinned asset on the enrolled simulator host. From
`integrations/simulation`, use the existing simulator enrollment directory and a directory containing
model files named by their profile's SHA-256:

```sh
uv sync --frozen --extra managed
uv run --frozen --extra managed python -m convoy_sim.qualification.runner \
  --data-dir /path/to/simulator-enrollment --assets /path/to/robot-assets
```

`--once` handles one pending request and exits (0 for passed/idle, 1 for a failed check, 2 for a
transport/configuration error). Enrollment and credentials use the existing agent. The runner does
not download URLs or control physical motors. Automatic artifact installation is still pending.

MuJoCo verification requires the profile's exact engine version and an MJCF file, or a ZIP bundle
with `model.xml` at its root. Bundle references must be internal; native plugins are unsupported.
Checks cover the installed digest, model compilation, declared actuated joints/types/limits,
direct position/velocity/torque controller mappings, control cadence relative to the physics step,
bounded native physics execution without numerical warnings, and actual rendering of declared RGB
cameras. Joint-state sensors are supported; other sensor kinds and controller adapters need explicit
implementations. A native engine runs in a separate process with a 60-second verification deadline.

Reports are immutable and tied to a requested profile digest, device binding and request generation.
An authenticated runner reports its evidence; this is not independent hardware attestation. Passing
does not establish successful policy execution, real-time deadlines, calibrated dynamics, or
authorization to move a physical robot. Simulation time and elapsed time are shown separately.

Routes:

- `POST /api/v1/robots/{id}/qualification`: operator requests a check with normal idempotency.
- `GET /api/v1/robots/{id}/qualification`: owner reads its latest result.
- `GET /api/agent/v1/registry`: enrolled device receives only its robot/profile/request.
- `POST /api/agent/v1/qualifications/{id}/report`: that device submits its terminal evidence.

PostgreSQL requires migration `0006_robot_qualification`; SQLite creates the additive table at
startup. The management and device endpoints retain their existing authentication boundaries.

Acceptance includes actual HTTP enrollment of physical/simulated identities, shared-profile
registration, a native MuJoCo subprocess, persisted movement evidence, restart without duplicate
execution, and a deliberately altered asset that must fail. Separate checks render a real camera,
reject unstable physics, and exercise lifecycle/ownership on SQLite and PostgreSQL. These checks
exercise simulator readiness; no learned policy or Jetson timing result is claimed by this suite.
