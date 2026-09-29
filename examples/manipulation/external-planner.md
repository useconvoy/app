# One external planner trial

This command keeps the API, private mission signer, action worker and MuJoCo simulator local. The planner must already be running behind verified HTTPS and remains entirely caller-owned. The command creates no containers/cloud resources and never starts, stops or replaces the remote service. Use [the isolated AWS template](../../infra/aws-planner-trial/README.md) if that is the selected provider; account access, hosting evidence and spending approval are separate prerequisites.

Use the existing qualified Python 3.12 environment with the LeRobot `paired` dependencies and pinned local action assets. The command selects this checkout's source paths for itself and every local child, verifies package/asset pins, and runs offline with the unchanged action runtime. Do not retarget an existing environment's editable installs merely to switch checkouts.

From the repository root, create a **fresh private directory under local `work/`, not a shared/output directory**:

```sh
python examples/manipulation/external_planner.py prepare --authority-dir /absolute/private/work/trial-authority
```

Preparation refuses any existing directory, including partial installations. Preserve failed preparation for diagnosis; choose a fresh directory only for a deliberately new trial. It creates:

- `api-keys/execution.json`: private signer; stays local and is passed only to the API.
- `verification/planner.json`: public planner verifier; deliver this document to the remote planner's `CONVOY_PLANNER_VERIFICATION_JSON` secret reference.
- `verification/action.json`: public local-action verifier.
- `planner-probe.txt`: private probe token; deliver its contents through the selected provider's secret workflow and retain this file for the local client. Never put the token in command arguments or logs.

Deploy the exact immutable paired manifest using the qualified image's release-injection contract. Declare remote CPU placement for a real remote trial; a local HTTPS qualification can retain local placement. Keep the private signer on this computer. The CLI validates both public exports against it before contacting the endpoint; creating different credentials after deploying the public document will not work. For a real trial, retain the authority until all missions are reconciled and the remote service is stopped. It is not automatically deleted or rotated.

Once the caller has verified the endpoint, image and release:

```sh
python examples/manipulation/external_planner.py run \
  --authority-dir /absolute/private/work/trial-authority \
  --manifest /absolute/path/release.json \
  --planner-url https://planner.example.com \
  --action-assets /absolute/path/verified-smolvla-assets \
  --output /absolute/private/work/trial-run-01 \
  --direct-report /absolute/path/direct-report.json \
  --direct-trace /absolute/path/direct-trace.jsonl
```

For a private test CA, add `--planner-ca-file /absolute/path/ca.pem`; otherwise system certificate validation applies. There is no insecure mode. Both direct-baseline files are optional together. When supplied, success requires exact action equality, successful direct and managed tasks, and matching step counts. The run starts exactly one seed-0 mission with the existing budgets, requires an accepted plan matching the observed serving identity, and verifies actual task success. A declared remote placement does not establish AWS/cloud location; retain task/image/account evidence separately.

The output directory must be new. `external-result.json` records the safe result, durable plan, comparison and action-trace hash; `pipeline/` retains the local API/coordinator journal, actions and diagnostic logs. Failure evidence is kept and the command exits unsuccessfully. The authority and remote endpoint survive either outcome; only the local pipeline's children are cleaned up. Keep both directories private, reconcile uncertain missions before trying again, and perform remote teardown through its caller/approved infrastructure workflow. SIGTERM/interrupt requests local cleanup; an uncatchable host/process loss still requires explicit recovery.
