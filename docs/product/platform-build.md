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
