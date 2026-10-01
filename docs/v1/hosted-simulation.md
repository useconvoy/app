# Hosted workspace with a local learned-policy runner

The public workspace owns projects, deployments, missions and evaluations. A
separately enrolled simulator runs Qwen, SmolVLA and MuJoCo together on the existing
Mac in offline lockstep. Keep the Mac awake and its runner active to start new
missions. Completed results and uploaded camera recordings remain available from
other browsers even when the Mac is offline. This is not Jetson real-time policy
qualification, a cloud GPU deployment, or a physical robot controller.

## Existing AWS host

Run `python3 infra/existing-host/enable-simulation.py` as the host administrator
on the existing Compose installation. It preserves a configuration backup,
initializes persistent action/planner signing authority if absent, enables
simulated enrollment, and starts a separate evaluation process using the current
API image and database. No AWS resources are provisioned. The job process has a
192 MiB memory limit, 0.5 CPU limit and bounded logs. A subsequent normal release
updates its image alongside the API; rollback restores its prior definition.

Only the API mounts `portal/execution-signing` read-only. The job, browser and
simulator never receive the signing key. Copy only the two public documents from
`portal/execution-verification` to the runner. Established keys are retained;
missing or conflicting authority stops setup rather than silently rotating it.

## Runner

Use the already qualified Python environment with the paired learned-policy
extra and the installed `local-recipes.json` registry. Model weights and native
binaries are checked by the existing bundle owner; this command downloads none.

```
python examples/manipulation/hosted.py enroll \
  --server https://deployconvoy.com \
  --state /private/hosted-simulation \
  --registry /private/installed/local-recipes.json \
  --login-file /private/login.json
python examples/manipulation/hosted.py serve \
  --state /private/hosted-simulation \
  --verification /private/public-verification
```

The login file contains `email` and `password`. It is used only during setup and
can be removed afterward. Setup retains an idempotency namespace and enrollment
receipt for interrupted provisioning. It registers the project under that user,
the simulated robot, application and installed releases. It starts no mission.
The supervisor runs the existing coordinator and a separate recording uploader.
Use an OS service manager for restart after login/process failure; do not run two
coordinators for the same state directory. Its generated credentials remain local.
The simulator's 2-second management clock uncertainty allowance is conservative
configuration, not measured clock synchronization or a real-time guarantee.

## Hosted flow

Under Applications, select **Simulation lab · Mac runner**, **Virtual Sawyer ·
Mac (offline simulation)**, and **Qwen + SmolVLA · puck pick-and-place**. Request a
release deployment; wait for Ready. Start a seed-0 mission with 300 seconds of
authorization. Open View episode after completion, then Watch the simulation.
Qwen selects the single supported skill; SmolVLA supplies camera-conditioned
commands. Viewing speed does not describe original inference speed.

Release qualification uses the normal evaluation worker: create a fixed-seed
suite, evaluate A and B, compare, explicitly promote a passing result and
optionally require it for deployment. A/B use the same weights and different Qwen
context lengths. Repeat seed 0 qualifies this demonstration, not generalization.

## Remote recordings

The enrolled device uploads only terminal, acknowledged camera missions through
`/api/agent/v1/robots/{robot}/missions/{mission}/recording/commands/{sequence}`,
then requests `recording/publish`. Each request is size-bounded and requires the
current device binding and the matching immutable episode. The server constructs
its own recording database; it never accepts a client path or uploaded SQLite.
Commands are immutable, repeated uploads are idempotent, and publication verifies
all action identities, ordering, camera availability and observation continuity.
Only complete recordings become visible. Existing project authorization protects
both metadata and frame reads. Local mounted-journal playback still works.

The uploader persists acknowledged progress and retries after disconnection;
transfers cannot apply actions and never block the control loop. Server storage
is capped at 512 MiB (`CONVOY_RECORDING_QUOTA_BYTES`), including partial uploads.
When full, recording transfer stops with an explicit quota failure; mission
outcomes remain available. Automatic retention and object-store migration remain
future work. Preserve the coordinator journal until publication is verified.
