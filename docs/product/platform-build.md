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
