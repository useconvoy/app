# First execution pipeline: management, coordinator, inference

Status: implemented for the MetaWorld simulator; acceptance results are recorded in the progress log.

## Decision

Extend the existing FastAPI service with the robot application lifecycle. Keep its existing user sessions, enrollment credentials, revocation checks, transaction discipline, and legacy text-model routes. Application releases have a distinct identity from the existing GGUF releases.

Run policy inference in a separate process. The coordinator communicates directly with it, without a database or management API request per action. The worker loads an operator-selected Python runtime factory and refuses to serve if its reported artifact/runtime identity differs from the immutable release. Customer factories must verify the actual checkpoint contents they load. An authenticated readiness probe cannot authorize action inference.

For the first installation, the API and worker share a deployment-configured execution signing key. The API issues a short-lived grant scoped to device, robot, mission, boot, coordinator incarnation, authority epoch, and release digest. The worker verifies it locally. Probe credentials are separate. The local coordinator owns the actual execution decision and rechecks original monotonic deadlines, observation identity, cancellation, and authority before a simulator step. The worker cannot determine freshness by comparing a robot timestamp to its own clock.

One active inference call is allowed at this worker. Saturation is rejected rather than accumulating an action queue. This is a measured starting constraint, not a fleet scalability claim. A runtime that blocks indefinitely requires process supervision; an HTTP timeout alone does not terminate its computation.

The coordinator holds an exclusive process lock and journals mission/command identity before execution. A restart with unresolved work reports `unknown` and blocks a conflicting mission. It does not infer that a missing acknowledgement means an action never happened. Cancellation is a request until acknowledged by the coordinator. It cannot retroactively undo a simulator step already executed.

## Storage and ownership

Use the existing SQLite server for this local milestone. New project resources are owner-scoped; legacy installation APIs are still shared. This is **not** the multi-customer tenancy or qualified Postgres implementation from the platform design. Additive schema version 4 permits migration from the prior demo while preventing older server versions from opening a database they do not understand.

A deployment means ready and idle; it does not start a mission. Release content is immutable and addressed by SHA-256. Mutations use durable idempotency receipts, generations prevent stale deployment requests, and terminal episodes cannot be silently replaced by later reports. Raw step evidence stays in the local robot journal for now; the API receives bounded episode summaries. Object-store upload, retention and evaluation comparisons remain subsequent work.

## Hosting and cost

The acceptance harness starts three loopback services/processes on the development machine: API, inference worker, and robot coordinator with MuJoCo. No GPU or cloud account is needed for the scripted reference. All processes stop when the harness exits. A private output directory retains database, journals and logs; it also contains local credentials and must not be published wholesale.

TLS is required by the coordinator outside loopback. Hosted topology remains web/API/jobs on CPU, separate inference capacity, Postgres and object storage as described in the implementation plan. No new cloud resource is provisioned by this change. Keys configured through environment variables are for the local milestone; hosted secrets need deployment-managed rotation and distribution. A shared signing key does not isolate mutually untrusted worker hosts.

## What this qualifies

The initial runtime is the pinned MetaWorld scripted expert using privileged simulator state, not a trained VLA or a cloud reasoning model. Physics runs in lockstep: it waits for the policy reply. Separate processes and real HTTP establish the software path; they do not establish an 80 Hz real-world control deadline or WAN feasibility. The policy adapter and its action semantics must be qualified again for a learned checkpoint, another robot, a paired System 1/System 2 architecture, or different compute.

The next product milestones remain the console workflow, learned-policy qualification, component pairing, release evaluation/promotion, Postgres/jobs/artifact storage, and a budgeted provider deployment. No feature in the broader design is declared complete merely because this bounded reference runs.
