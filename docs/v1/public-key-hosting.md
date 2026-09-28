# Public-key service packaging qualification

The Compose installation and unapplied AWS template now carry PR #90's authority
separation across service boundaries. Only the API receives private mission
signing keys. The action worker receives its public verification document;
database jobs receive neither. The service/runtime contract remains unchanged.

This is local packaging qualification, not an AWS deployment. It uses a scripted
Sawyer pick-and-place policy with real MuJoCo physics. The separate learned
Qwen/SmolVLA evidence remains in [execution signing](execution-signing.md).

## Implementation

`infra/runtime/execution_keys.py` is the shared bootstrap boundary. Compose uses
a one-off initializer and two persistent volumes. Serving containers mount only
their own document, read-only. Initialization validates an existing pair and
refuses partial, mismatched or missing established keys. A persisted host receipt
prevents automatic replacement after both volumes disappear. Legacy HMAC state
is retained; a separate project/state directory is required for a new installation.

AWS task execution roles can fetch only their assigned secret documents. API and
inference wrappers consume injected JSON into validated private task files before
serving; they reject wrong-role, legacy or conflicting execution settings. The
JSON is removed from the application environment. This does not conceal it from
trusted ECS/host administrators or refresh an already running task after rotation.
See [AWS staging](aws-cpu-staging.md) for the restart/drain procedure.

## Recorded acceptance

On September 28, 2026, clean source `0674a2312644a9e15d8f6f0126b5aa5a05479812`
passed the isolated Compose acceptance. Images were built from the implementation
bytes committed at that revision. The [machine-readable receipt](../../examples/manipulation/evidence/public-key-hosting.json)
records image identities, source revision, outcomes and private evidence hashes.

- Two ordinary console/API missions completed successfully, each with 500 applied
  actions in the durable device journal. TLS verification, browser-origin checks,
  PostgreSQL metadata, device readiness and idempotent start were exercised.
- A full down/up between missions preserved both key-file hashes, the first
  completed mission and its 500 journal rows. No mission started automatically.
- Actual container checks verified the expected read-only key mount paths and
  API-only private file setting; rendered Compose configuration tests checked
  role-to-volume assignments. The worker's public file contained no private key.
- Both stored mission JWT signatures verified at their original issuance time.
  This historical check does not admit an expired mission or establish
  byte-for-byte token preservation across restart.
- Removing both key volumes from this disposable installation made the next
  startup fail with exit code 2. No replacement keys or serving containers were
  created. Final cleanup removed its containers, volumes, network and private
  installation state. All eight pre-existing containers retained their exact
  IDs, start times, PIDs and restart counts throughout.

Both rebuilt AWS derivative images also passed locally against disposable
PostgreSQL: bootstrap and migrations, runtime DML, denied runtime DDL, legacy route
fencing, API-private key materialization, and exact mission claim/reclaim with the
original expiry. The actual inference wrapper accepted a signed action, rejected
planner/HMAC grants and conflicting key configuration, completed its shutdown
lifespan and closed its listener. An initial test-only assertion assumed a zero
shutdown status; it now accepts Uvicorn's expected SIGTERM exit only after checking
completed application shutdown. The failed attempt is retained separately.

The helper's 28 focused tests, four Compose configuration checks, Terraform real
provider validation and three mocked plans passed. Ruff and whitespace checks
passed. Hosted GitHub Actions results are recorded on the PR separately; these
local checks are not a claim that hosted CI ran.

## Remaining work

Build and qualify the real Qwen planner for Linux ARM64, then host it behind
verified HTTPS and pair it with local action inference. AWS account access,
regional cost review and deployment inputs remain prerequisites to a paid
deployment. The Jetson still requires accepted SSH authentication and separate
hardware/model qualification. This slice establishes no WAN timing, real-time
control, broader-seed reliability or multi-tenant isolation guarantee.
