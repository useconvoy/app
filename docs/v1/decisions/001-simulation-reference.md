# 001: Start action-policy integration with a bounded manipulation simulation

Status: accepted for the first implementation slice. September 27, 2026.

## Context

The user selected robot-arm manipulation as the first simulation workload. The
platform should eventually run learned action policies as well as slower cloud
planners. Simulation must execute actual robot/controller/physics behavior; a
text traffic generator or canned task response is not sufficient evidence.

## Decision

Use pinned MetaWorld 3.1.1 / MuJoCo 3.3.0 `pick-place-v3` as the first CPU-capable
reference. Keep it in an optional `integrations/simulation` package. Run upstream's
scripted Sawyer policy and a zero-action baseline, recording seeds, observations,
actions, outcomes, configuration, package versions, and optional video. The policy
boundary accepts a separately supplied implementation.

This validates a real manipulation loop. It does not validate learned weights,
camera perception, cloud timing, or physical transfer. The first runner is offline
lockstep, with this limitation encoded in every manifest. A learned policy must
match the environment's action parameterization, observations, normalization,
frequency, and model dependencies before its outcome is meaningful.

## Alternatives

| Option | Role |
|---|---|
| MetaWorld/MuJoCo | Chosen first: manipulation tasks, controllers, scripted baselines, native CPU development and focused CI. |
| LeRobot + LIBERO | Next candidate for pretrained visual action policies; current integration requires Linux and an explicitly qualified checkpoint. |
| robosuite/robomimic | Useful manipulation ecosystem; some public pretrained checkpoints target legacy runtimes, so version matching is necessary. |
| Isaac Sim / Isaac Lab | Strong later choice for humanoids, richer scenes, and GPU-parallel simulation. Dedicated compatible GPU infrastructure should follow the selected workload. |
| Gazebo/Nav2 | Retain for the mobile-robot/cloud-planner application. It is not the first manipulation-policy reference. |
| The Construct | Commercial browser/cloud robotics workspaces can reduce interactive environment setup. Our reproducible runner remains independent of a hosted IDE. No subscription is required for this first slice. |

Official references: [MetaWorld](https://github.com/Farama-Foundation/Metaworld),
[LeRobot LIBERO](https://huggingface.co/docs/lerobot/libero),
[robomimic checkpoint version warning](https://robomimic.github.io/docs/model_zoo/robomimic_v0.1.html),
[Isaac Lab](https://github.com/isaac-sim/IsaacLab),
[The Construct](https://www.theconstruct.ai/).

## Hosting, cost, and iteration

Run headlessly on the developer machine and Linux CI initially. No new paid
infrastructure or robot hardware is required. Optional rendering has its own
dependencies. Simulator, model worker, and Convoy management are separate
deployment concerns: selecting a simulator does not select a cloud provider.

Revisit the environment when the first customer's embodiment, sensors, contact
task, or pretrained checkpoint requires another one. Add a second integration
with the same evidence lifecycle; do not rewrite the core around MetaWorld's
39-element observation.

The existing hosted deployment is a demo. Production branch protection and
enterprise hosting are not prerequisites for these slices. Keep ordinary test
automation and avoid modifying the demo incidentally.

## PR sequence

1. Relevant v1 branch checks using the existing test jobs.
2. This executable simulator reference, focused tests, and evidence files.
3. One qualified learned policy and explicit transforms/checkpoint identity.
4. Policy worker separation and real-time impairment evaluation.
5. Evaluation-job/API/UI integration and application release comparison.

Each PR targets `v1` and states its evidence and remaining scope. Keep dependency
changes, generic platform contracts, and unrelated UI work out of the simulator
PR. Review and integrate tested slices before broadening the support matrix.
