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
