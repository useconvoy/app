# Decision 003: service and hosting boundaries

Status: implemented locally for the scripted simulation profile. Cloud provider
selection and hosted production qualification remain open.

## Decision

Package independently operated processes while retaining the existing modular
control-plane application. A container boundary follows authority or resource
ownership, not every Python module.

| Process | Owns | Does not own |
| --- | --- | --- |
| Web / console BFF | Browser UI, origin checks, forwarding the user's session | Operator credential, database, device actuation |
| Management API | Human/device authentication, project resources, immutable releases, desired deployment and mission state | Model inference or robot control loop |
| Scheduler (`convoy-server worker`) | Existing fenced schedules, rollout maintenance and retention | Policy inference, simulated physics, new evaluation orchestration |
| Inference worker | One pinned release/runtime, grant validation, bounded decision admission | Database access, physical command submission |
| Device coordinator / simulator | Device credential, execution journal, deadline checks, observation/action exchange, simulator ownership | User account credential or database access |
| PostgreSQL | Fleet metadata, auth records, desired/observed state, mission/episode records | Model weights, videos, device's local command journal |

The scheduler is a long-running service because its existing lease loop already
supports process ownership. A durable evaluation executor can be a second jobs
entry point once its schema and lifecycle are implemented. It should create and
observe ordinary missions; it must not import MuJoCo or run untrusted model code
inside a database-privileged job process. A broker is not required just to split
these processes.

One API, one scheduler, one inference worker and one coordinator are the qualified
local topology. PostgreSQL currently serializes application writes with an
advisory transaction lock. Legacy chat state and artifacts remain on the API's
filesystem; API/scheduler share that volume. Adding replicas does not make those
features shared or prove horizontal scalability. The device's journal remains
local SQLite with one coordinator owner.

The scripted reference image can serve both inference and simulator because
their pinned libraries are identical. They still run independently: stopping
inference cannot transfer ownership of the simulator or its credential. A learned
policy gets its own qualified image and accelerator allocation; its larger model
environment must not become a mandatory dependency of the API/web/jobs images.

## Network and authority

The browser calls the same-origin BFF. The BFF reaches the API at an operator-set
internal HTTPS URL while forwarding only the caller's session. Robot and worker
traffic uses its separate device/execution credentials. Public URL configuration
never doubles as container DNS discovery.

TLS is verified across API/device/inference links. The local database remains
on a private Compose network without a host port; a hosted database requires
verified TLS and restricted network access. Provider VPCs, private endpoints or
an overlay can change reachability without changing the execution protocol.
The device initiates outbound traffic; a cloud ingress rule need not expose a
robot-side controller to the Internet.

The API supports [private Ed25519 signing and public verification](../execution-signing.md)
with purpose, issuer, audience and key rotation, retaining identity, expiry and
release binding. The local activation harness selects this mode. Scripted Compose
and unapplied AWS templates still use shared HMAC keys; migrate those templates
and qualify isolated identities/mounts before crossing provider or customer trust
boundaries. Encryption alone does not solve this authority boundary.

Inference requests go directly from the coordinator to the worker, keeping the
management database outside each decision. Management outages do not erase an
already granted bounded mission; local cancellation/deadline/late-result checks
still decide whether an action can be applied. Readiness timestamps describe an
acknowledgement, not a permanent health or safety guarantee.

## What a hosted pilot still needs

The local service setup is a reproducible functional qualification environment,
not production orchestration. Before an external customer pilot, provision:

- A container registry and deployment identity, immutable image references,
  release promotion/rollback and vulnerability/build provenance checks.
- A DNS name and trusted HTTPS ingress for web/API, separate internal endpoints,
  network policy and a managed secret store. Use separate runtime and migration
  database roles rather than the local development superuser.
- Managed PostgreSQL with encrypted connections, measured backups and a restore
  drill following the existing quarantine/grant-rotation runbook.
- Object storage for artifacts/evaluation media, content-digest verification,
  scoped upload/download credentials and explicit retention. Existing filesystem
  artifacts and text-chat state need a deliberate migration or single-host limit.
- CPU capacity for API/web/jobs and the selected provider's GPU/CPU worker
  capacity. Pin the model, runtime, observation/action profile and measured
  latency budget before choosing accelerator size or claiming compatibility.
- Central logs/metrics, actionable alerts, API and job health checks, worker
  saturation/latency measures, device connectivity evidence and an operator
  recovery procedure for unknown mission outcomes.

The first hosted pilot can use a single managed container platform plus managed
PostgreSQL and object storage. Kubernetes, multi-region failover and a generic
provider scheduler should follow actual deployment requirements and evidence.
Provider adapters should provision a qualified worker contract, not reinterpret
robot action semantics. The simulator remains a replaceable device adapter; a
physical deployment still requires the customer's controllers, calibration,
local safety system and explicit capability qualification.
