# Managed visual action policy

This optional integration runs the public **SmolVLA MetaWorld checkpoint** through Convoy's real enrollment, deployment, mission, inference, and episode APIs. The model worker and MuJoCo robot simulator are separate processes. A rendered image, four proprioceptive values, and the task instruction cross the inference boundary for every action. No training is needed.

Two consecutive managed missions on the same warm worker succeeded on seed 0 in **54 actions each**, with every action exactly equal across mission resets and to the previously recorded direct inference run. They took **47.0 and 42.2 seconds wall time** for **0.675 seconds of simulated control time** each under local load. Worker inference p50 was **735 ms**, p95 nearest rank about **975 ms** across 108 decisions. This establishes a working integration for one task/seed; it does not establish real-time control, general model quality, physical safety, or hardware portability. See [the qualification evidence](../../examples/manipulation/evidence/smolvla-managed-seed0.json).

## Install and run

Use a separate Python 3.12 environment. This integration pins MetaWorld 3.0.0; the existing scripted simulator pins 3.1.1 and remains a separate environment/profile. Python dependencies take several GB, including PyTorch. The checkpoint is 906,712,520 bytes; small base-model configs/tokenizer files add roughly 5 MB. Base VLM weights are **not** downloaded. Model peak RSS in the direct Mac qualification was about 2.51 GiB; allow additional memory for the API and simulator.

From this directory:

```sh
uv sync --frozen --extra managed
# One explicit download. Choose an asset directory outside the checkout.
uv run --frozen --extra managed python -m convoy_lerobot.fetch --output /absolute/path/convoy-smolvla-assets
export CONVOY_SMOLVLA_ASSETS=/absolute/path/convoy-smolvla-assets
uv run --frozen --extra managed python -m convoy_lerobot.acceptance --output runs/visual-first --repeat 2
```

Already-downloaded assets can be reused by setting `CONVOY_SMOLVLA_ASSETS` to their root, containing `checkpoint/` and `base-assets/`. Every expected file is hash-verified before loading. The worker loads all policy weights with `strict=True` and executes a warmup before readiness. The warmup's synthetic image only checks the runtime; the acceptance episode uses actual rendered images.

The acceptance runner creates temporary loopback API/worker services, enrolls a simulated device, registers a robot and immutable release, waits for ready/idle, explicitly starts the mission, and records its outcome. It stops all processes on exit. It generates credentials in the output directory: keep that directory private, and share only `visual-result.json` / `actions.jsonl`. Two repetitions use seed 0 to check that mission reset produces identical trajectories. `--direct-report` and `--direct-trace` optionally compare a prior direct qualification without running it again.

For individually hosted services, run the standard worker with `--factory convoy_lerobot.runtime:smolvla` and a release from `convoy_lerobot.artifact.reference_manifest()`. Run the enrolled simulated robot with `python -m convoy_lerobot.managed --data-dir ... --robot-id ... --worker-url ...`. Credentials and TLS expectations are the same as the [scripted managed example](../simulation/README.md). Do not point the scripted adapter at the visual profile.

## Exact profile

`metaworld-smolvla-pick-place-rgb-v1` fixes:

- LeRobot 0.6.1, MetaWorld 3.0.0, MuJoCo 3.3.0, task `pick-place-v3`.
- The upstream `MetaworldEnv` `corner2` camera at `[0.75, 0.075, 0.7]`, with both image axes flipped, rendered at 480×480 RGB. Wire input is noninterlaced RGB8 PNG, at most 1 MiB compressed. Width, height, chunk order/CRC, inflated size, and row filters are checked before the image decoder.
- Raw state entries 0–3 (end-effector position and gripper opening), cast to float32 for the saved preprocessor. Instruction: `Pick and place a puck to a goal`.
- Saved checkpoint processors/tokenizer and float32 CPU inference, four PyTorch threads, seed 0 reset per mission, `n_action_steps=1`. Each newly accepted observation causes fresh inference; predicted chunks are never passed off as decisions from later observations.
- Saved action unnormalization followed by explicit float32 clipping to `[-1,1]`; four normalized Cartesian/gripper commands go to MetaWorld's controller and physics. This clipping is part of the artifact identity, not an implicit platform repair.
- Stop on the first upstream benchmark success or configured horizon (at most 500 steps). Success is the upstream goal-distance criterion; it is not a claim of stable placement/release on a physical surface. The wrapper captures the terminal observation and internally resets; Convoy does not infer or execute another action after termination.
- A 5-second original per-decision deadline and a maximum 300-second mission grant in the reference release. CPU inference is offline lockstep; simulator time advances only after a valid result arrives.

The checkpoint's feature metadata lists state length 6 and three cameras. Its saved normalizer contains four state/action values, and the upstream runtime supports only the present camera with `empty_cameras=0`. This integration uses the actual upstream four-state/one-camera path that was executed in qualification; it does not synthesize additional state or cameras.

`artifact.py` hashes weights, saved processors, tokenizer/config assets, pinned package versions, camera/profile semantics, reset seed, one-action replanning, and action clipping into a runtime artifact digest. Changing any of these requires a new artifact; changing profile semantics requires a new profile version.

## Stateful inference boundary

A signed mission grant binds robot/device/mission, release digest, boot ID, coordinator incarnation, authority epoch, and original expiry. `POST /v1/sessions/start` binds one owner and resets the policy exactly once. An exact start retry returns the current sequence without resetting. Decisions require consecutive sequences and unique request/observation IDs; no output cache is replayed. A failed or ambiguous inference poisons that session.

There is one runtime admission slot and no inference queue. `POST /v1/sessions/end` can fence in-flight work without waiting for inference. Closed mission grants are retained until their original expiry, including a close received before start. Retention is bounded and never evicts live fences. A new mission cannot reset while another inference runs. Worker restart loses session state and rejects continuation until a new session is admitted; the coordinator's durable journal prevents command replay and requires explicit recovery for interrupted missions.

The robot coordinator persists command intent before stepping physics, validates the entire echoed request identity and original monotonic deadline before execution, and reports terminal outcomes without re-running commands on delivery retries. None of this certifies a physical controller.

## Tests and infrastructure

Contract/session/coordinator tests use no weights or renderer. The optional runtime reset test imports no PyTorch. Ordinary PR checks should run those tests plus the existing scripted physics pipeline. Keep the learned acceptance as an explicit qualification job with a pinned cache and a wall-time budget; do not download the checkpoint on every PR.

The qualified run used an Apple Silicon Mac with no GPU service. Linux CPU can use MuJoCo's EGL backend with an available driver or OSMesa software rendering (`MUJOCO_GL=osmesa`, with the OS Mesa library installed). The complete learned profile has not yet been qualified on Linux CI. A hosted deployment can give the policy its own Linux CPU/GPU service, but GPU execution is a different runtime configuration that needs separate latency and trajectory qualification. No paid infrastructure is required for the local reference. The action-policy service location is independent of the simulator/coordinator URL: a future qualified Jetson runtime can serve edge inference. That requires a separate supported dependency/device configuration and artifact digest. A cloud System 2 planner is a separate service and is not implemented in this integration; a general text model is not treated as a motor policy.

## Upstream provenance

- [Checkpoint and model card, pinned revision](https://huggingface.co/lerobot/smolvla_metaworld/tree/cd6778d2cfa724c1bf5fc637490548e54d81dc4c): Apache-2.0; full weight SHA-256 `6d3ce9f40627a247fa1c289517ec67da495810a58305c9bb1ec8588dfda0a736`.
- [Base tokenizer/config revision](https://huggingface.co/HuggingFaceTB/SmolVLM2-500M-Video-Instruct/tree/7b375e1b73b11138ff12fe22c8f2822d8fe03467).
- [Upstream environment, camera, actions and success handling](https://github.com/huggingface/lerobot/blob/v0.6.1/src/lerobot/envs/metaworld.py).
- [SmolVLA model and action queue implementation](https://github.com/huggingface/lerobot/blob/v0.6.1/src/lerobot/policies/smolvla/modeling_smolvla.py).
