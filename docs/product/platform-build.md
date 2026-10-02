# Project-to-robot execution build

User objective: build the agreed project, registration, fleet, configuration/deployment,
task/chat, simulation, timing and characterization flows. A physical robot must have a
simulation representation derived from the same versioned profile. This document tracks
implementation evidence, not a reduced definition of completion.

## Required outcomes

- [ ] Project-first entry point and migration/linking of existing configuration documents.
- [ ] Independent robot identity; onboard computer enrollment and capability discovery.
- [ ] Immutable robot profiles with geometry, joints, sensors, actuator/controller contracts,
      provenance, uncertainty and MuJoCo/Isaac asset identities.
- [ ] Physical robot -> simulated instance using the same profile, with explicit readiness.
- [ ] Fleet membership in onboarding and selection; compatibility checks and staged deployments.
- [ ] Configuration revisions bound to backend releases, model artifacts, runtime, placement,
      observation/action interfaces, timing, fallback, deployment activation and rollback.
- [ ] Task start/cancel, adapter-aware pause/resume, acknowledgements and durable event timeline.
- [ ] Chat and API tasking share the same structured, authorized task lifecycle.
- [ ] Natural-language scenario creation from validated templates; preview and pinned seeds.
- [ ] MuJoCo colocated on Jetson with independent physics and policy processes.
- [ ] Isaac Sim runner on supported hardware with the same observation/action/task contract.
- [ ] Timing qualification, fault injection, replay and real/simulation clock distinction.
- [ ] Fleet rollout reports actual revisions, unavailable members and failures.
- [ ] Characterization estimates hidden simulator parameters and validates held-out trajectories;
      supervised physical calibration only with functioning interfaces and known movement bounds.
- [ ] Browser journeys, contract checks, CPU simulation and actual hardware qualification evidence.

## Architecture decisions

Extend the existing API, database, worker, agent and website. Keep execution authority on the
backend and robot, never in the saved UI document. Keep legacy records and recordings readable.
Use additive schema changes with explicit PostgreSQL migrations. Registration does not authorize
movement. A simulation asset declaration is not proof of a calibrated digital twin. Preserve
immutable profile revisions when the physical robot and simulator refer to the same mechanics.

## Work slices

1. Project/robot/profile/fleet records and first project UI.
2. Enrollment, discovery and workspace-to-platform links.
3. Configuration validation, deployment and task controls.
4. Timing experiment integration and Jetson verification.
5. Simulator adapter contract, Isaac runner and scenario/task language interface.
6. Fleet rollout and characterization experiment.

## Verification and infrastructure

Use existing hosting for management. Do not provision an unattended GPU. Existing hosted
simulation remains pinned while development uses a separate checkout. Isaac acceptance requires
supported GPU hardware; do not report an unavailable hardware test as passing. Record each PR's
checks and remaining requirements here as work proceeds.

## First implementation slice

Branch: `codex/project-robot-profiles`. Adds real project navigation, physical/simulated registration,
immutable profile revisions and simulation lineage, caller-owned connection selection, and fleet
creation/assignment/removal. It preserves legacy configurations, recordings and simulator execution.

Evidence so far: 287 server tests pass (5 environment/conditional skips); 29 selected real-PostgreSQL
registry/migration/lifecycle tests pass; 25 browser tests pass covering the new onboarding journey and
existing configurations/device/replay behavior. Frontend platform (18) and configuration (69) tests,
TypeScript, lint, token check and production build pass. The final form presentation and caller-owned connection query were rechecked in the browser and
on PostgreSQL.

Remaining within milestone 1: configuration document -> platform identity reconciliation. New registry
records cannot yet execute: runner qualification and artifact loading are the next required execution
work, not a completed feature. No public deployment or cloud resource provisioning performed.

Concurrent work observed: PR #112 adds actual Jetson Qwen calls to the bimanual task. Keep this work
independent and reconcile that PR when implementing the unified experiment runner; its simulated-time
latency accounting is not by itself our independent real-time physics qualification. PR #101 remains
the separate Jetson timing experiment to integrate and verify.

## Simulator-readiness slice

Branch: `codex/robot-simulation-qualification`, stacked on the registry work. Adds device-bound
verification requests, immutable reports, a MuJoCo asset/controller/camera checker in a bounded
native subprocess, and the Project → Robots verification controls with automatic result refresh.
Profile lineage now reaches a real engine instead of ending at an asset declaration.

Evidence: the actual HTTP/agent/runner pipeline registers physical and simulated identities against
one profile, executes the imported model and records movement, survives runner restart, and rejects
an altered installed asset. Eleven native engine tests include actual camera rendering and unstable
physics rejection. Nineteen selected SQLite/API lifecycle tests, 51 PostgreSQL registry/migration/
evaluation/document tests, and the updated project browser journey pass. Frontend types, lint,
platform proxy tests and production build pass. CI now runs the HTTP pipeline with the managed extra
and uses software OpenGL for native camera verification.

Remaining: automatic enrollment/artifact installation from the project UI; configuration identity
reconciliation; model deployment and task execution for newly registered robots; independent
real-time timing qualification; Isaac execution; scenario/task language; staged fleets and physical
characterization. Simulator-readiness success does not bypass these unfinished execution requirements.
No hosted rollout, AWS resource provisioning or physical movement was performed in this slice.

## Registered model execution slice

Branch: `codex/registered-robot-execution`, stacked on simulator verification. A schema-3 release pins
the robot profile/model, joint names/order/bounds/cadence, policy artifact and a joint-target task.
The existing coordinator, inference worker, session protocol, deployment/mission APIs and durable
journal now support that interface alongside the legacy Sawyer profiles. Registration no longer
ends in an unconditional execution block: matching verified simulated joint-position robots can run.

Project robot rows open a detail page with deployment selection, acknowledged readiness, task
start/stop, and result summaries. A pending stop is distinct from an acknowledged cancellation.
The selected release must match the deployed release before Start is enabled. Execution APIs retain
the final admission checks, including current verification at claim.

Actual acceptance: HTTP enrollment → shared physical/simulation profile → native verification →
release deployment → real worker and coordinator → target reached in MuJoCo. Further tasks exercise
in-flight cancellation and rejection of an out-of-range action before movement. The initial runtime
is explicitly a controlled joint-position reference, not a learned policy. The execution mode remains
functional lockstep. See [setup, contract and remaining work](registered-execution.md).

This advances deployment and task execution but leaves the overall objective active. Configuration
document/release linking, automatic installation/onboarding, learned edge/cloud pairing for registered
robots, natural-language task/scenario creation, real-time timing, Isaac, fleet rollout and physical
characterization remain outstanding. No production deployment or new cloud resources were created.

Verification: 166 selected contract/worker/coordinator/API tests, 32 PostgreSQL lifecycle tests,
12 native simulator/managed-pipeline tests, 18 frontend platform tests, and both project browser
journeys pass. Frontend type checks, lint and production build pass; browser checks cover accessibility
and mobile overflow. The preceding registry and qualification PRs (#113/#114) also have all CI checks
passing. This slice still needs its own remote CI before merge.


## Project configuration releases slice

Branch: `codex/project-configuration-releases`, stacked on registered execution. The Project
Configurations tab and existing Configurations routes now create authoritative application/release
records, revise them immutably and link a chosen release into the robot deployment/task controls.
The server derives the interface from an owned immutable profile and rejects wrong projects,
missing/extra targets, unsupported controllers and out-of-range positions before creating records.
Installed-policy identity is supported alongside the explicitly controlled reference; neither choice
claims automatic installation. Setup downloads retain the server's canonical JSON numeric representation.

The real HTTP/MuJoCo acceptance now creates its release through this configuration API and consumes
its downloaded setup strings before running the actual worker/coordinator, success, cancellation and
invalid-action cases. This is still functional lockstep simulation, not learned-policy or real-time
qualification. Earlier PR #115 now has all nine remote CI checks passing.

Remaining: explicit legacy workspace-document links/migration, automatic enrollment and installation,
learned edge/cloud pairing, independent timing, scenario/chat, Isaac, fleet rollout and characterization.
The existing hosting and AWS resources are unchanged.

Verification for this slice: 13 selected API/SQLite lifecycle tests, six PostgreSQL configuration and
qualification cases, the actual HTTP/worker/MuJoCo pipeline, 18 platform frontend checks and 32 browser
journeys pass. Browser checks include release creation/revision/download, preservation of earlier
releases, navigation with the exact selected release, existing device/replay flows, accessibility and
mobile overflow. Type checks, lint, design-token checks and the production build pass. Remote CI for
this branch still needs to run. The disposable PostgreSQL container and local test services were stopped.

## Managed registered-worker slice

Branch: `codex/managed-registered-worker`, stacked on configuration releases. An enrolled registered
simulator can use `--manage-worker` to prepare the validated joint-reference artifact and launch its
owned local policy worker. No per-release policy file, worker command or manually supplied probe token
is needed. The coordinator's existing idle/mission-recovery gate controls preparation; exact live
worker identity is verified before activation. Unsupported policy preflight retains the prior loaded
worker. Startup failures are persisted and require a new deployment generation before retry, including
after runner restart. Owned-process records, bounded logs and verified shutdown reuse the agent's
existing process-owner implementation. External operator-managed policy workers remain supported.

Actual acceptance now covers automatic reference-worker creation, task success, switching to an
opposite-target release, blocking an unavailable runtime, restoring an earlier release and verified
worker shutdown. A separate actual-child failure case checks persistent retry fencing. These and the
native model checks pass (13 tests); 24 coordinator bundle/recovery checks also pass. This mode still
uses functional lockstep physics and a reference controller, not learned manipulation or real-time
qualification. No physical motors, hosted rollout or new cloud resources are involved.

Remaining: robot asset delivery and onboarding, general learned model installation, old configuration
identity migration, real-time edge/cloud experiments, Isaac, scenario/chat tasking, staged fleet
rollout and dynamics characterization. The full objective remains incomplete.

Frontend verification: the configuration creation/revision/deployment browser journey passes after
updating the setup guidance, including accessibility and mobile overflow. Lint and production build
(including type checks) pass. The managed CI job now includes the real startup-failure/retry case.
This branch still requires remote CI before merge.

## Registered real-time timing slice

Branch: `codex/registered-realtime-timing`, stacked on managed workers. Configuration creation/revision
now accepts an explicit timing contract. The registered runner can advance native MuJoCo physics in
an independent process at the declared wall-clock cadence while the policy worker runs separately.
Timestamped observations and action-time admission enforce freshness; expired targets fall back to
simulated position hold. Physics overruns are recorded even when detected during cleanup. Command
journaling distinguishes applied actions from confirmed rejection. Cleanup failure remains unknown.

The robot's task-result view separates task success from timing evidence and reports policy wait,
observation-to-result/action age, physics dispatch/completion lag, physical simulation ticks and fallback
counts. Short runs show insufficient evidence. Existing functional lockstep releases remain supported;
existing offline promotion gates do not accept these real-time reports as functional evidence.

Verification: five real-physics timing cases cover expired observations, expired targets, scheduler
pauses detected during capture or cleanup, and a sustained run that meets observed deadlines while
failing the task. The HTTP enrollment/worker/coordinator/native-MuJoCo pipeline covers both a successful
fast worker and a deliberately delayed HTTP policy with physics continuing during the wait. Sixty-one
contract/coordinator/recovery checks, six PostgreSQL configuration/qualification cases, three project
browser journeys, frontend lint/type checking and production build pass. Browser checks include the
timing contract, results, accessibility and mobile overflow; the generated mobile result was inspected.

The preceding managed-worker PR's container check exposed a stale expected migration revision.
Updated that assertion on its own branch and verified the real API container against disposable
PostgreSQL: migration, runtime-role data access, denied schema changes and mission grants pass.
No application database or hosted service was changed. Remote CI for the new timing branch is pending.

Remaining: Jetson qualification; repeated timing evaluation/fault scenarios; camera-conditioned learned
policies and replay for registered models; edge/cloud planner pairing; automatic asset/model installation
and onboarding; legacy configuration reconciliation; chat/scenario generation; Isaac; fleet rollout;
and physical dynamics characterization. Local joint-state/reference results do not prove learned-policy
quality or calibrated physical fidelity. This is a completed implementation slice, not completion of
the overall objective. No public deployment, physical movement or new cloud resources were performed.

## Jetson registered-runtime verification

Branch: `codex/jetson-registered-qualification`. Reached the existing Jetson through its hostname using
the existing SSH key; the earlier IP timed out. Installed a separate frozen environment and executed
the actual native timing cases and complete HTTP enrollment/qualification/deployment/worker/coordinator
pipeline. Both complete runs passed six tests. The recorded run retained seven bounded measurement
reports, with source hashes verified after retrieval. CI now retains the same measurement files.

The managed reference reached its target with 59.10 ms p95 observation-to-action latency; its short
run remains insufficient timing evidence. Delayed HTTP inference applied zero actions while physics
advanced. A sustained direct reference met its observed timing contract over 210 ticks while deliberately
failing task success. See [hardware evidence and limits](registered-timing-jetson.md).

This advances actual Jetson execution, not learned edge/cloud qualification. Camera policy integration,
remote planning, overhead/load characterization, robot/model asset delivery, project onboarding,
scenario/chat, Isaac, fleet rollout, legacy reconciliation and physical calibration are still required.
No hosted deployment or new cloud resources were created; the existing device agent remained running.

## Robot model delivery slice

Branch: `codex/robot-asset-delivery`, stacked on Jetson evidence. Project profiles now expose simulation
file upload, local fingerprint feedback and stored-file status. The API streams a bounded model to
profile-scoped storage, checks its pinned SHA-256 and rechecks the operator before publication. Only the
assigned simulated device may retrieve it. Qualification and deployment fetch a missing/damaged cache
from the authenticated control-plane origin, verify the bytes, then run the existing native checks.
Local pre-provisioned assets remain supported. Model upload alone never grants simulator readiness.

The actual HTTP/MuJoCo pipeline now starts with an empty asset directory, downloads during verification,
recovers a removed cache during deployment, runs tasks, restores a damaged local copy and rejects
corrupted server bytes. Five SQLite/API and five PostgreSQL upload/qualification checks pass, covering
ownership, assigned-device access, physical-device rejection, pinned bytes, quota, bounded bodies and
cleanup. Nineteen proxy/frontend checks and the three project browser journeys pass; mobile asset UI
was inspected. Seventeen native model/timing/pipeline cases, the transport regression, Python lint,
frontend lint/type checks and production build pass. These changes have not been deployed to the public app.

Current hosted file limit is 16 MiB with a separate configurable 1 GiB robot-model quota. Larger model
bundles, external storage and self-service storage management remain unfinished. This removes manual
copying for supported uploads, not the remaining enrollment, device discovery, learned-model installation,
cloud pairing, legacy migration, task/scenario language, Isaac execution, fleet rollout or calibration.
The earlier timing PR (#118) now has all remote CI checks passing. No new cloud infrastructure was used.

## Project connection setup slice

Branch: `codex/project-connection-setup`, stacked on asset delivery. Add robot now creates a short-lived
enrollment command, shows its status, supports cancellation and selects the claimed computer in the
registration form. The form displays bounded agent-reported hardware and distinguishes enrollment from
an actual heartbeat. Setup uses caller-owned project/enrollment records and the existing registration
assignment checks. Each command uses a separate data directory and safely quotes user-entered names.
No plaintext enrollment token is stored in a mutation receipt or returned by status reads.

Simulated-device setup can report the real host's inventory and sensors through `--host-inventory`,
while preserving simulator identity and legacy synthetic-demo defaults. The actual HTTP/native-MuJoCo
pipeline executes the generated enrollment CLI commands before profile assignment, asset delivery,
qualification, deployment and tasks. It verifies real host architecture and non-synthetic inventory.

Verification: 12 SQLite/API setup/registry tests, 11 corresponding PostgreSQL tests, nine agent hardware/
restart checks, the actual HTTP/native simulation pipeline, 19 frontend platform checks and three
project browser journeys pass. Browser coverage includes cancellation, automatic connection selection,
hardware details, accessibility and mobile overflow; desktop and mobile captures were inspected. Lint,
frontend type checks, token checks and production build pass. The prior asset-delivery PR (#121) now has
all nine remote CI checks passing. This slice still requires its own remote CI before merge.

Remaining: agent installation and combined runner startup; legacy configuration links; learned-policy
installation and edge/cloud pairing; repeated timing evaluation and registered-model visual replay;
scenario/chat tasking; Isaac execution; staged fleet rollout and physical characterization. This setup
does not scan mechanics or command a physical robot. No hosted rollout, new cloud resources or changes
to the Jetson's running agent were made.

Follow-up hardware acceptance: the actual Jetson passed the generated enrollment-command pipeline
in 53.52 seconds, then the extended version in 41.90 seconds. The latter also executes the displayed
agent startup command with `--once` and verifies that the simulator connection becomes online, retains
real host inventory, and reports non-synthetic clock provenance before model verification and tasks.
The existing long-running agent remained at PID 1047. Tests used isolated loopback endpoints and a
separate directory, with no public application records. Runtime source is commit `061f0ef`; the added
heartbeat test's SHA-256 is `8039201abc8400d096d158c22ab34186d96bc8e74395fcb05dd13b95264c09cd`,
verified against the local file after transfer. Full pipeline elapsed time is a test duration, not
a policy-latency measurement. These checks still use a reference controller rather than a learned policy.

## Combined simulator service slice

Branch: `codex/registered-simulator-service`, stacked on connection setup. Simulator onboarding now
offers a single foreground service command. The service starts connection health reporting before
registration, then performs requested model verification and watches for deployments/tasks. Existing
coordinator journals, admission checks, managed worker ownership and independently paced physics remain
the execution mechanisms. Model verification is interruptible and waits for local execution/recovery
to settle. Duplicate service owners are rejected. A committed-source installer builds a private frozen
runtime without the API server or test dependencies; immutable source copies allow later checkout edits.

Added authenticated public action-key delivery. The API exports no private key or HMAC secret; public
keys are refreshed from the enrolled origin and checked before publication. Rotations keep the same
issuer/audience; private signing authority stays on the API. Existing explicit local trust configuration
still works. The generated service command assumes this runtime is installed and the API has signing
configured; merely enrolling a computer never proves those conditions.

Local verification: 20 native simulator, timing, managed worker and actual HTTP pipeline tests pass,
including service startup before registration, duplicate rejection, real model/task execution, key
rotation, forced owner loss, orphan recovery and graceful stop. Fifteen API signing/connection tests,
23 coordinator/transport checks and the three project browser journeys pass. Browser coverage checks
the generated simulator command alongside physical enrollment. Lint, type checks and production build
pass. The committed-source installer was exercised locally, followed by the full service test using
its separate runtime Python (14.56 seconds). API-server and pytest packages are absent from that runtime.
CI now installs the same distribution and runs that acceptance path too.

The actual Jetson passed the separate-runtime service test in 42.25 seconds: startup before registration,
live heartbeat, native model qualification, signed tasks, public-key rotation, duplicate-owner rejection,
forced process loss, owned-worker recovery and graceful shutdown. Runtime source is `b8bf22a`;
the service SHA-256 is `3f329934f25b6269c187070ed5d47a6b394a125a4152a8312419116cdb9cd5f8` and the
separate-runtime test SHA-256 is `5af243e4d7baa35aa51364e5a26d156a3efe98c55f1978f215ac6b5dc700043c`,
both checked against the copied files. Jetson runtime dependencies were installed from the frozen
runtime extra, separately from the API/test environment. The installer script itself was exercised
on the Mac; the archived Jetson source does not contain a Git checkout. No API/test packages are present
in either runtime. The existing Jetson agent remained at PID 1047 and no test processes remained.
These are end-to-end test durations, not action-latency measurements. Remote CI for this slice is pending;
the preceding connection setup PR (#122) has all nine checks passing.

This advances onboarding and local operation. Learned-model installation and edge/cloud pairing,
legacy configuration reconciliation, scenario/chat tasking, registered visual replay, Isaac, fleet
rollout and characterization remain required. No public rollout or cloud resources were created.

## Saved workspace configuration links

Branch: `codex/project-workspace-links`, stacked on the simulator service. An owner can explicitly link
a saved workspace configuration to an executable configuration in an owned project. The saved setup
now opens the project's release/deployment controls, and the executable configuration links back to
the original specification and results. Linking, reviewing and unlinking do not rewrite the workspace,
create robot identities, install policies, change immutable releases or deploy anything.

The API stores the association separately, with the source configuration fingerprint and document
revision. Source edits, missing configurations and unsupported document formats are reported rather
than silently propagated into execution. Unrelated workspace activity does not invalidate the source
fingerprint. Writes require an operator, current document revision and expected link identity; durable
receipts make retries idempotent. Replacement produces a new link identity so an old unlink request
cannot remove its replacement. Ownership covers both the source document and destination application.
Sample workspaces are never linked. Deleting a document removes its links while preserving applications.

PostgreSQL requires additive migration `0007_workspace_links`; SQLite creates the additive table using
the existing startup mechanism. Verification: three link lifecycle/authority tests and 17 existing
document/configuration API tests pass on SQLite; 43 selected PostgreSQL link, migration, workspace,
evaluation and lifecycle checks pass, including schema/ORM parity. Twenty platform proxy checks and
nine browser journeys pass, covering linking errors, navigation, preserved results, changed-source
status, unlinking, existing configurations, accessibility and mobile overflow. The mobile rendering
was inspected and its spacing improved. Frontend type checks, lint, token checks, production build and
server lint pass. The preceding simulator-service PR (#123) now has all ten CI checks passing.

This is explicit navigation reconciliation, not automatic translation of legacy model names or robot
declarations into a runnable deployment. A newly created runnable configuration still requires profile,
policy and target choices, followed by explicit linking from the saved setup. Broader project navigation,
learned-policy installation and pairing, visual replay, task/scenario language, Isaac, staged fleets and
characterization remain unfinished. No hosted rollout, new AWS resources or physical motion occurred.

## Registered physics trajectory and local replay reconstruction

Branch: `codex/registered-trajectory-replay`, stacked on workspace links. The registered CLI and
combined service capture initial state plus every completed physics control tick, including held
policy commands and fallback ticks while inference is unavailable. Bounded in-memory state copies
occur inside measured physics; rendering and file writes stay outside the control loop. The terminal
report pins a locally saved trajectory by SHA-256. Recording export failure leaves task outcome intact.

`convoy-sim-render` checks trajectory, release, robot-model and engine identities, then restores each
recorded pose in an isolated MuJoCo renderer. It produces real PNG frames, hashes, action provenance,
timestamps and observer-camera metadata without stepping physics or rerunning a policy. Frames are
explicitly observer reconstructions, not the images seen by a policy. The hosted upload/API/player
integration remains unfinished; this slice establishes its recording and rendering source.

Verification: native tests cover moving poses and actual image differences, independent physics/held
targets/fallback, digest and model mismatch rejection, discontinuity rejection, immutable outputs and
bounded local storage. The actual HTTP/worker/coordinator pipeline verifies recorded identities and
tick counts for functional success, timely real-time control and a policy timeout with zero admitted
actions. A deliberate recording-directory failure preserves a successful task and reports the export
failure. The combined service's successful tasks also verify their exported trajectory identity.
Twenty-two coordinator regression cases pass; simulator and agent lint pass.

The Jetson ran commit `9076b97` in `/home/jetsy/convoy-experiments/trajectory-9076b97`, with a separate
frozen Python 3.11 environment and EGL rendering. All nine trajectory/timing/HTTP-pipeline cases passed
in 61.13 seconds (test-suite duration, not inference latency). Source hashes match local files:
`trajectory.py` = `d91b9db2cf475ec5507449eac6be5357fd81e627b07346571b5e5dcf1a314e2e`;
coordinator `engine.py` = `86933c3513a46e969501cf1d2f5ac013a8274132ad339fad6342a4b7c933a9d6`.
No isolated test processes remained; the original device agent stayed running at PID 1047. Local initial
and final rendered frames were inspected. No public deployment, cloud provisioning or physical motion.

Next required integration: upload completed recordings independently of control execution and expose
the registered trajectory in the existing robot task-result player, preserving timing/action provenance.
The full product scope, including learned edge/cloud models, task/scenario language, Isaac, staged fleets
and characterization, remains active and incomplete.

## Integrated project workspace and release

Branch: `codex/unified-project-workspace`. Reconciles the verified execution stack with
main's Jetson planner and robot-preview work. Projects now provide persistent Overview,
Robots, Profiles, Fleets, Configurations, Simulations and Runs navigation. Registered
robots expose computer details and execution controls; existing simulator profiles keep
their existing policy/evaluation controls. Workspace device diagnostics are accessible
from Robots and explicitly identify the configured device rather than pretending it is
an arbitrary registered robot.

Saved model setups can be explicitly assigned to a project without replacing their
robots, evaluations, traces or replay references. Existing URLs still resolve. Project
context follows assigned setups into robot and evaluation pages. New model selections
remain drafts; executable releases still require the supported profile/policy contract.
Project placement is excluded from the execution-link specification fingerprint, while
cross-project link creation is rejected. Unassigned older setups remain accessible and
can be organized from a project's Configurations section.

Simulations lists the project's runners; Runs exposes task results, timing evidence and
available existing episode replay, plus the assigned setups' evaluation history. Local
registered trajectory files still require a future hosted upload/player integration.
Isaac, natural-language scenarios, automatic learned-policy installation/pairing, staged
fleet rollout and physical characterization are not enabled by this UI integration.

Validation before release includes the existing registration/deployment/cancellation,
configuration, evaluation and replay browser journeys; a new assignment-preservation
journey with mobile/accessibility checks; a draft-creation/no-dispatch journey; and an API
regression for project placement and execution-link isolation. The deployment smoke check
now expects `/app/projects`, fixing the stale redirect expectation that rolled back #113.
Deployment uses the existing Lightsail host and existing runtime data, with no new AWS
resources. Release CI and public verification are recorded in the integration PR.

## Project directory, fleet navigation and robot configurations

Branch: `codex/project-fleet-workspace`. Projects use a dedicated list alongside
a separate creation panel. Project pages and their robot/configuration details
share a persistent left sidebar, with fleet dropdowns linking to member robots.
The overview groups robots by fleet and shows each robot's current configuration
and deployment acknowledgement instead of a separate list of deployment links.
Unassigned robots remain visible and can be grouped from the Robots page.

Robot configuration controls resolve the actual deployed application and release
for the robot's current generation. Operators can select compatible alternatives
and explicitly deploy them; task start waits for the current release to be ready.
New robots without a deployment display an unconfigured state and setup controls.
Saved model drafts remain distinct from executable releases. Existing legacy
policy/evaluation tools remain available in an expandable section.

Validation includes desktop/mobile project and fleet navigation, accessibility,
creation and reload, deployment selection without accidental dispatch, and
switching the configuration on the existing local MuJoCo runner. The release
retains the existing hosted data and uses the existing Lightsail infrastructure.
