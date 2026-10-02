# Managed manipulation reference

Run a real Convoy management API, separate inference worker, and local robot coordinator against the MuJoCo Sawyer simulation. The reference policy is **scripted**; it has no trained weights and reads simulator state. Physics waits for each decision.

From `integrations/simulation`:

```sh
uv sync --frozen --extra managed
uv run --frozen --extra managed python ../../examples/manipulation/pipeline.py --faults --output runs/managed-reference
```

Use a new output directory on each run. The harness generates local credentials, enrolls the simulator through the normal device API, creates a project/application/release, deploys it, waits for readiness, and starts a mission. It verifies a duplicate start cannot create a second mission, then exercises acknowledged cancellation and abrupt coordinator restart. Unresolved work is reported as unknown and blocks a conflicting mission. The API, worker and coordinator are independent processes with real HTTP between them.

`pipeline-result.json` contains the human-readable acceptance record and API episode summaries. The output directory also holds the server database, worker/coordinator logs and the robot execution journal. **The directory contains local credentials**; share a reviewed result file, not the entire directory. Processes are cleaned up even when an assertion fails. No cloud infrastructure is created.

The worker can host another installed runtime factory with `runtime`, `artifact_sha256` and `get_action(observation)` attributes; it must match the registered release. Load and hash the actual checkpoint in that factory. Changing the policy does not imply its observation/action semantics match this environment. The only implemented execution profile here is the pinned 39-value MetaWorld observation and four normalized displacement/gripper actions.

For component configuration, read [the execution decision record](../../docs/v1/decisions/002-execution-pipeline.md). The authenticated management contract is visible at the API's `/api/docs` route. The general multi-customer/cloud platform remains under implementation.

To use the workspace interactively, pass `--serve` instead of `--faults`. The stack
stays ready and idle until interrupted. A private `connection.json` contains its
local API URL and generated development login. Start the website with that
`CONVOY_API_URL`, then open the seeded robot's page under Projects and its
**Advanced policy and evaluation tools**. The API, policy worker and coordinator all
remain separate processes. Stop the harness to shut them down.

For the single live browser acceptance after building the website, run from the
repository root:

```sh
node website/scripts/test-live-console.mjs integrations/simulation/runs/YOUR_STACK/connection.json NEW_BROWSER_OUTPUT
```

The browser runner starts its own web process, signs in on the seeded robot's page, redeploys, runs a real
500-step episode, checks its task outcome, requests cancellation, verifies mobile
layout and signs out. It stops its web process afterward. Ordinary CI includes
this integration, plus the headless management/inference outage scenarios.

## Use a disposable PostgreSQL database

Start the [local PostgreSQL service](../../docs/v1/postgresql-development.md), then
run from `integrations/simulation`:

```sh
CONVOY_TEST_POSTGRES_URL='postgresql+psycopg://convoy:convoy-local-only@127.0.0.1:55432/convoy_dev' \
  uv run --frozen --extra managed python ../../examples/manipulation/pipeline.py \
  --postgres --faults --output runs/postgres-reference
```

`--postgres` explicitly opts into the database named by
`CONVOY_TEST_POSTGRES_URL` as an administrative connection endpoint. That identity
needs permission to create databases. The harness creates a new random
`convoy_pipeline_*` database, applies the packaged migration, runs the real API,
worker and simulator, then drops **only that newly created database** after its
processes stop. It never migrates, clears or drops the configured endpoint
database. The flag also works with `--serve`; stopping the interactive stack
discards its PostgreSQL fleet state.

The six acceptance cases cover a successful task, redeployment and idle restart,
cancellation after actions, continued local execution during management outage
and later report replay, stopping actions when inference disappears, and abrupt
coordinator loss that requires explicit recovery. These use real HTTP and physics
with the same assertions as the SQLite run. The result identifies the database
backend, server/schema versions and cleanup outcome without recording the URL or
database credentials. The existing offline-lockstep/scripted-policy limits apply.

Normal completion, failed assertions and handled interrupts clean up the task
database. An uncatchable process/host loss or database outage can prevent cleanup;
`postgres-database.json` records the owned database name for manual recovery.
The ordinary SQLite mode still retains its database in the output directory;
neither mode changes the robot's local SQLite execution journal.

PostgreSQL CI runs the ten database-boundary cases, one real database
ownership/collision/abort check, and this six-case managed pipeline. It uploads
only `pipeline-result.json`, never the surrounding credential directory.
