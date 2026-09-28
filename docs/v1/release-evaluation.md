# Repeatable release evaluation

Convoy can now allocate and execute a fixed simulation suite through the normal
robot deployment and mission path. Each case is persisted before execution. A
separate `convoy-evaluations` process reconciles the jobs; the existing coordinator
and inference worker perform the actual simulation and inference. The job process
never imports model weights or runs customer code. The local service deployment
starts it as the separate `evaluations` container, alongside the legacy scheduler.
Its container health check verifies database/schema access; job progress remains
observable through the evaluation API.

## Workflow

1. Create an immutable suite under an application with
   `POST /api/v1/applications/{id}/evaluation-suites`. Supply `name`,
   `reference_release_id`, distinct `seeds` (1–20), and `min_successes`.
   Convoy copies the reference release's interface, environment and execution
   envelope into the suite. Only its policy may vary between candidates.
2. Start a run with `POST /api/v1/evaluations` and `suite_id`, `release_id`,
   `robot_id`. The robot is reserved until the run finishes or its unresolved
   execution is reconciled. The configured inference worker must already serve
   the candidate release; this slice does not provision or hot-swap that worker.
3. Run `convoy-evaluations` using the same database configuration as the API.
   Multiple processes may compete for jobs. A persisted lease epoch fences a
   stale process. Every step and mission allocation commit together; a restarted
   job observes the original mission rather than admitting its replacement.
4. Inspect `GET /api/v1/evaluations/{id}`. The report includes every allocated
   case, immutable episode links, release/suite digests, success counts and wall
   duration. `?baseline_id={id}` compares another run of the same immutable suite.
5. Promote a passing run with `POST /api/v1/evaluations/{id}/promote` and `{}`.
   Configure an application gate with
   `POST /api/v1/applications/{id}/evaluation-gate`, supplying `suite_id` and
   `expected_generation` (initially 0). Future ordinary deployment and mission
   admission require a promotion for that exact release and current suite.
   Evaluation jobs themselves may run candidates to obtain that evidence.
6. Request cancellation with `POST /api/v1/evaluations/{id}/cancel`. An active
   mission must acknowledge cancellation. Unknown execution keeps the robot
   reserved; the job never assumes a missing report means no action occurred.

These are authenticated, project-scoped APIs. All writes require the normal
`Idempotency-Key`; browser requests also require the existing client header.
Console evaluation controls are a follow-up; the current console's deployment
and Start actions already enforce any configured gate at the API boundary.

Robot snapshots include `evaluation_id` when an active or unresolved evaluation
reserves the robot. `GET /api/v1/applications/{id}/qualification?release_id={id}`
returns the current gate, its matching promotion (if any), and whether the
qualification condition permits deployment. It does not replace robot readiness,
mission/reservation, role or installation checks. These authoritative reads avoid
inferring admission from a truncated list of recent evaluation runs.

The job retains the initiating credential's identity, not its secret. Revocation,
expiry, disabling the user, or losing the operator role stops admission of new
cases and requests cancellation of the active case. Signing out revokes a
session-backed job's authority. Use a scoped automation identity for unattended
runs once that identity model is implemented; current API tokens inherit the
user's installation role and are not a multi-customer tenancy boundary.

## Scoring and interpretation

The pinned `final-success-v1` scorer counts a case only when its normal mission
completed, the reported seed/release match, the execution mode is offline
lockstep, and the final environment success flag is true. Operational failures
count against the allocated sample count. Cancelled/incomplete evaluations never
pass. A case cannot be retried inside a run; a retry is a new full evaluation.

Reports rely on the authenticated coordinator's episode summary. They are not
independent replay attestations. Two fixed seeds demonstrate reproducibility,
not statistical generalization. Wall duration includes simulation/inference
execution; it does not qualify real-time robot deadlines. Changing the simulator,
observation interface, horizon, stopping rule or timing envelope requires a new
suite, so unlike measurements cannot be silently promoted as a comparison.

An application gate is explicit and optional for local development. It affects
new admissions, not work already authorized on a robot. Changing the gate does
not cancel an in-flight mission or rewind physical state.

## Reproduce the acceptance

```sh
cd integrations/simulation
uv sync --frozen --extra managed
uv run --frozen --extra managed python ../../examples/manipulation/evaluate.py \
  --output runs/evaluation-new
```

This starts real API, job, inference and coordinator processes. It runs seeds 0
and 1 twice through MuJoCo, kills/restarts the job process, verifies that no
extra mission appears, checks that an unpromoted deployment is rejected, then
promotes a passing report and deploys it. It uses a scripted policy to exercise
release operations without downloading weights. Learned inference is qualified
separately by the visual-policy adapter.

Only `evaluation-result.json` is shareable. The surrounding private directory
contains generated credentials and journals. The harness stops its processes
on exit. The live database retains immutable runs, cases and episode links;
object-store capture and retention are separate pending work.

PostgreSQL needs explicit migration `0002_evaluations` before starting the updated
API and job service. SQLite schema 4 upgrades additively to 5. PostgreSQL tests
exercise the prior revision upgrade, schema parity, competing worker admission,
stale-lease rejection and the same lifecycle contract as SQLite.
