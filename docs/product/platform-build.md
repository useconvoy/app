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
