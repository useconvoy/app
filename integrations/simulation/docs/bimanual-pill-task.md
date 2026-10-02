# Bimanual pill task (`convoy_sim.bimanual_pill_task`)

A MuJoCo simulation of a wheeled bimanual station robot putting 20–30 capsule pills
into an open supplement bottle, five planner/policy deployment configurations,
seeds × slices evaluation, and recordings in the platform's replay formats: the
offline evaluation import (`scripts/import_offline_eval.py`) and the hosted
coordinator journal.

Scope, stated plainly:

- **Physics, kinematics and contacts are simulated** with MuJoCo 3.3.0 at SI units.
- **Skills read simulator state** (pill and bottle poses). They are a scripted,
  privileged-state skill library like the MetaWorld scripted reference in this
  package, not perception or a learned policy. The exception is the cloud vision
  planner's pick, which goes to the table point a model pointed at in the camera
  image and reads no pill pose (below).
- **The cloud vision planner decides from pixels, with real model calls.** Every
  skill decision of `cloud_luna_vision` ("Cloud GPT-6 Luna (vision)") is a call to
  GPT-6 Luna (OpenAI Responses API, low reasoning effort) with the rendered head
  camera image. The model answers with a pixel to pick at; the pixel is
  back-projected with the same camera's depth to a table point and a scripted IK
  grasp goes there. Reaching, lifting and the transfer stay scripted
  ([below](#the-cloud-vision-planner-real-calls-from-pixels-vision_plannerpy)).
- **The edge device planner's decisions are real model calls.** Every skill
  decision of `edge_qwen_edge_skills` is a request to the model of a connected
  device's active release through Convoy's device chat API. Which model that is
  (repository, file, quantization, runtime) is read from the platform when the run
  starts and recorded with it. Its latency is measured, not modeled, and nothing
  stands in when a call fails ([below](#the-edge-device-planner-real-on-device-calls-device_plannerpy)). The model reads text only,
  so it gets the scene as text computed from the simulator state, not camera images.
- **The other three configurations use one deterministic rule-based stand-in.**
  They differ only in where the planner runs, its latency distribution (from
  measured or published numbers) and what a network outage does. Nothing claims a
  language model's planning quality for them.
- **SmolVLA is reported unavailable** for this robot. The only local checkpoint is
  the single-arm MetaWorld policy; it is not trained on this embodiment, so the
  configuration that uses it places no pills rather than borrowing competence.

## Run

From `integrations/simulation`, Python 3.11 and uv:

```sh
uv sync --frozen --extra video --extra managed     # video: GIF/PNG previews; managed: platform replay reader
uv run --frozen pytest -q tests/test_bimanual_pill_task.py tests/test_device_planner.py
uv run --frozen convoy-sim-pills episode --config cloud_astra_only --slice nominal --seed 0 --output runs/one
uv run --frozen convoy-sim-pills check-physics --output runs/physics.json

# Rendering needs OpenGL. Headless Linux: MUJOCO_GL=glfw under xvfb-run (or EGL/OSMesa).
MUJOCO_GL=glfw xvfb-run -a uv run --frozen convoy-sim-pills episode --record --preview-camera photo \
    --config cloud_astra_only --slice network_outage --seed 2 --output runs/recorded
MUJOCO_GL=glfw xvfb-run -a uv run --frozen convoy-sim-pills evaluate --seeds 0-4 --jobs 4 \
    --configs edge_qwen_cloud_astra,cloud_astra_only,edge_smolvla_cloud_astra \
    --record edge_qwen_cloud_astra:nominal:0,cloud_astra_only:network_outage:2 --output runs/matrix
uv run --frozen convoy-sim-pills verify runs/recorded     # platform replay rules + convoy_server's reader
MUJOCO_GL=glfw xvfb-run -a uv run --frozen convoy-sim-pills render --output runs/stills

# The modelled demo matrix in the offline import format (one directory per configuration), then the import:
MUJOCO_GL=glfw xvfb-run -a uv run --frozen convoy-sim-pills evaluate \
    --configs edge_qwen_cloud_astra,cloud_astra_only,edge_smolvla_cloud_astra \
    --slices nominal,network_outage,pill_count_30 --seeds 0-2 --seed-stride 100 \
    --record all --replay offline --jobs 4 --output runs/demo
python scripts/import_offline_eval.py --dry-run runs/demo/cloud_astra_only
CONVOY_SERVER=https://deployconvoy.com CONVOY_EMAIL=… CONVOY_PASSWORD=… \
    python scripts/import_offline_eval.py runs/demo/cloud_astra_only

# The edge device planner: real calls to the model on a connected device, through the website's
# device chat contract (platform-chat-v1) with an operator session. CONVOY_PLANNER_DEVICE (or
# --planner-device) names the device; without it the run uses the only physical device listed.
CONVOY_SERVER=https://deployconvoy.com CONVOY_SESSION_FILE=~/.convoy-session CONVOY_PLANNER_DEVICE=dev_… \
MUJOCO_GL=glfw xvfb-run -a uv run --frozen convoy-sim-pills evaluate --configs edge_qwen_edge_skills \
    --slices nominal,pill_count_30 --seeds 0-4 --seed-stride 200 --record all --replay offline \
    --output runs/edge-device

# The cloud vision planner: real gpt-6-luna calls from this machine with the head camera image. The key
# comes from OPEN_AI_API_KEY (never printed); every call's cost goes to the spend ledger, and no call is
# sent once the ledger's spend plus the call's worst case would pass --spend-cap-usd (default 3.00).
OPEN_AI_API_KEY=… MUJOCO_GL=egl uv run --frozen convoy-sim-pills evaluate --configs cloud_luna_vision \
    --slices nominal,pill_count_30 --seeds 0-4 --seed-stride 200 --record all --replay offline \
    --spend-ledger runs/spend.jsonl --output runs/cloud-luna-vision

# The device chat contract end to end, without a device: the real control plane, a production build
# of the website (`next start`), a device on the agent's chat relay with a scripted model, and the
# planner's transport (needs Node and the website's dependencies).
(cd ../../website && pnpm install --frozen-lockfile && pnpm build)
uv run --frozen --extra managed pytest -q tests/test_platform_chat_contract.py
```

Every command writes to a new directory and refuses to overwrite a recording.
`scripts/pill_task_eval.sh` runs the modelled demo matrix (not the edge device planner).

## Robot model (`robot.py`)

A generic wheeled bimanual robot: white/grey palette, about 1.30 m tall and 49 kg.
Values are engineering estimates for this class of robot (two 6-DOF harmonic-drive
arms, 5 kg combined lift); inertias are explicit solid boxes/cylinders of the stated
masses, so totals are auditable (`test_model_matches_the_specified_robot_and_scene`).

| Part | Geometry (m) | Mass (kg) |
|---|---|---|
| Wheeled base | 0.52 × 0.46 × 0.20 rounded box, 2 drive wheels Ø0.15, 4 casters | 27 (incl. battery) |
| Edge-computer enclosure (Jetson Orin Nano class) on the base | 0.13 × 0.11 × 0.045, vents, status LED | 0.6 |
| Column | 80 × 80 mm aluminium extrusion with T-slots, 0.27–0.86 m | 1.2 |
| Torso | white rounded shell (ellipsoid 0.22 × 0.29 × 0.33), T crossbar at shoulder height, 3 blue logo dots | 8.0 |
| Head | pan/tilt neck; white box 0.12 × 0.17 × 0.115 (top at 1.30 m), dark face display, 2 green eye lights, RGB-D camera bar | 1.8 |
| Each arm | shoulder yaw axis at (0.08, ±0.17, 1.13); upper arm 0.30, forearm 0.30, wrist to flange 0.06, flange to TCP 0.126 (0.77 m shoulder to fingertip) | 5.55 incl. gripper |
| Each gripper | white printed housing and fingers, silicone pads, wrist RGB camera; parallel stroke 0–80 mm, 20 N per finger at saturation | 0.5 |

Arm joints (humanoid convention: zero = arm straight forward):

| Joint | Range (rad) | Torque limit (N·m) | kp / kv | Armature (kg·m²) |
|---|---|---|---|---|
| shoulder yaw | −1.5 … 2.2 | 30 | 300 / 25 | 0.08 |
| shoulder pitch | −1.0 … 2.0 | 60 | 400 / 35 | 0.10 |
| elbow | −2.6 … 0.2 | 30 | 300 / 25 | 0.06 |
| forearm roll | −3.0 … 3.0 | 12 | 60 / 4 | 0.015 |
| wrist pitch | −2.0 … 2.4 | 12 | 60 / 4 | 0.015 |
| wrist roll | −3.0 … 3.0 | 6 | 30 / 2 | 0.008 |

The static load of a horizontal arm holding 2.5 kg at the TCP is ~36 N·m at the
shoulder pitch and ~17 N·m at the elbow, inside these limits. Armature is reflected
rotor inertia through ~100:1 harmonic drives. Gravity compensation is applied
through the actuators (`actuatorgravcomp`), so it counts against the torque limits.
Servo targets carry velocity feed-forward (`q* + kv/kp·q̇*`), which makes MuJoCo's
position servo a PD tracker without velocity lag. The gripper is one position servo
(1500 N/m, 30 N·s/m, ±20 N) on a tendon coupling both fingers, as a rack and pinion would.

Inverse kinematics is closed form (`control.ArmKinematics.analytic`): shoulder yaw
puts the wrist centre in the offset arm plane, shoulder pitch and elbow solve the
planar two-link problem (elbow down), and the roll–pitch–roll wrist takes the rest.
It matches MuJoCo's forward kinematics to 1e-15 m and reaches the whole pill region
(x 0.30–0.62 m, y ±0.30 m) with at least one arm at any grasp yaw. At transfer
height the wrist leans forward up to 0.55 rad, as a hand does over a container.

## Scene (`scene.py`)

- Table top 0.75 m (dark laminate), black rubber mat 3 mm (top 0.753 m).
- 20–30 white capsule pills: 20 × 8 mm (radius 4 mm, cylinder half-length 6 mm),
  0.5–1.0 g each (uniform per pill), rejection-sampled flat on the mat at least 2 mm
  apart, clear of the bottle and cap.
- White wide-mouth HDPE bottle, Ø65 × 110 mm, 1.5 mm wall, conical shoulder,
  45 mm neck with a 41 mm opening, 22 g, front-centre at (0.37, 0) between the arms.
  Collision geometry is 72 thin boxes plus a floor disc (MuJoCo collides convex
  shapes only; the convex hull of a bottle is solid); a smooth lathe mesh is drawn.
- Red Ø48 × 16 mm polypropylene cap (4.5 g) lying beside the bottle.
- Head camera (64° vertical field of view, 480 × 480), two wrist cameras, and photo,
  overview and top cameras for previews.

## Physics note (`physics.py`, measured by `check-physics`)

| Setting | Value | Why |
|---|---|---|
| Timestep | 2 ms (1 ms with `--timestep 0.001`) | The fastest pill in the task falls ≤15 cm (1.7 m/s, 3.4 mm per step), below the 4 mm radius. Crossing a 1.5 mm bottle wall needs 9.5 mm per step. |
| Integrator | implicitfast | Joint damping and servo kv are integrated implicitly, so stiff servos are stable. |
| Solver | CG, 200 iterations, tolerance 1e-10 | With ~25 free pills (nv≈174) and up to ~550 contact rows once pills pile in the bottle, Newton's dense Hessian cost 6–10 ms per step; CG cost ~1.5 ms with the same sub-mm/s residual creep. |
| Friction cone | elliptic, impratio 10 | Exact Coulomb cone; friction rows stiffer than normal rows, the standard setting for slip-free grasps. |
| Noslip iterations | 4 | Soft contacts let a squeezed capsule creep and turn between the pads; the noslip pass re-solves the friction rows without softness. With 0, 13 of 230 transfers in nine checked episodes (grasp clearance 1–2 mm) reached the bottle without their pill; with 4, none of 141 in six did, nor any of the 656 in the demo matrix, for ~15% more compute per step. |
| Contact solref | (0.004, 1) | MuJoCo contacts are springs in acceleration space, so one time constant suits a 0.7 g pill and a 6 kg arm. 4 ms is the stiffest stable value at 2 ms (≥ 2 steps). The 20 ms default lets a resting pill sink ~0.3 mm and wobble. |
| Contact solimp | (0.95, 0.99, 0.5 mm, 0.5, 2) | Impedance rises to 0.99 within 0.5 mm of penetration: near-rigid but well conditioned. |
| Pill contacts | condim 6 (sliding, torsion, rolling) | Without rolling resistance a capsule on its side rolls away after the lightest touch. |
| Mat collision | box extends 25 mm into the table top | A fast impact deeper than the 3 mm mat resolves upward instead of lodging between two overlapping boxes. |

Friction (MuJoCo takes the larger of the two geoms' values, so pills carry the lowest):

| Pair | μ sliding | Basis |
|---|---|---|
| pill–mat (rubber) | 0.60 | rubber on dry polymer 0.5–0.8 |
| pill–silicone pad | 0.95 | soft TPU/silicone on gelatin 0.9–1.0; torsional 1.6 mm from the larger patch |
| pill–pill | 0.30 | coated tablets on smooth polymer 0.2–0.4 |
| pill–printed finger | 0.35 | PLA/PETG |
| pill–bottle (HDPE) | 0.30 | HDPE ~0.25 against polymers |
| rolling / torsional (pill) | 8e-5 m / 4e-4 m | c_rr ≈ 0.02 × r on a compliant mat; ~(2/3)μa for a 1 mm patch |

The skills squeeze a pill with ~3 N per finger (commanded opening 2.5 mm under the
pill's width): 6 N of friction capacity for a 0.007 N pill. Soft-contact
penetration grows with force over the pill's tiny mass (~0.4 mm per pad at 3 N),
comparable to the compression of a silicone pad.

Measured with `convoy-sim-pills check-physics` (MuJoCo 3.3.0, Linux x86-64):

| Check | 2 ms (default) | 1 ms |
|---|---|---|
| 24 pills resting on the mat, 2 s: max speed / drift / penetration | 2.8e-6 m/s / 0.007 µm / 4.3 µm | 6.7e-7 m/s / 0.005 µm / 4.3 µm |
| Drop from 0.15 m (1.7 m/s, the task's worst case): tunnelled / centre below surface / peak penetration / speed after 1.5 s | no / no / 1.7 mm / 7.3e-6 m/s | no / no / 1.9 mm / 3.7e-6 m/s |
| Drop from 0.30 m (2.4 m/s): tunnelled / centre below surface / peak penetration | no / yes, 1.0 mm for one step / 7.1 mm | no / no / 3.4 mm |
| 30 pills dropped into the bottle: inside / below the floor / max speed after 2 s / pile height | 30 / 0 / 1.6e-4 m/s / 18 mm | 30 / 0 / 8.6e-4 m/s / 19 mm |
| Grasp a 1 g pill, lift, hold 2 s: grip force / opening / slip | 3.35 N / 7.23 mm / 0.002 mm | 3.35 N / 7.23 mm / 0.002 mm |

Pills piled in the bottle keep creeping at ≤0.9 mm/s with either step (soft
contacts between many capsules); a filled bottle left alone stays upright for 30 s
(tilt 0.001°) with CG or Newton. Faster impacts than the task produces (>2 m/s)
should use `--timestep 0.001`. Without the noslip pass the same checks gave the
same drops and fill, 0.4 µm of resting drift and 0.016 mm of grasp slip.

Two modelling bugs found while validating this, and fixed: (1) the robot root is
welded to the world, so MuJoCo's parent–child contact filter does not exclude the
shoulder mounts from the torso and they must be excluded explicitly; (2) the IK
must choose, of the two equivalent finger orientations, the one closest to the
current joints. Otherwise a path can flip the wrist half a turn mid-motion, and
the 84 mm gripper housing knocked the 22 g bottle over.

## Task and metrics (`pills_to_bottle`)

- Instruction: "Put all the pills in the bottle".
- A pill is **in the bottle** when its centre, in the bottle's frame, is within the
  31 mm inner radius and between the floor and the 110 mm rim.
- **Success**: every pill is in the bottle at episode end. **Progress**: the fraction
  placed (also the per-step reward in recordings).
- An episode ends when all pills are in (after 0.5 s of settling), when the planner
  says done or declines, or at the 150 s horizon (6 s per pill for 25 pills).
- Also reported: time until all pills are placed, planner calls/failures and latency
  p50/p95, skill outcomes, *operator pages* (no skill running for 8 s while work
  remains), peak non-pill robot–environment contact force and arm–arm contacts
  (both scanned after every physics step), protective stops, peak bottle tilt,
  pills lost off the table, rejected stale decisions, IK fallbacks.

## Skills (`skills.py`)

- `pick_and_drop(pill, arm)`: Bézier transfer to 5 cm above the pill → servo settle
  (≤3 mm tracking error) → descend until the fingertips are 2 mm above the mat
  (aborted by more than 2 N on the gripper from anything but the table and mat, as
  a wrist force sensor would flag, or 6 mm tracking error) → force-limited close → lift
  with a slip check → transfer over the mouth (needs the bottle zone; an arm whose
  reach blocks the zone carries the pill aside to wait) → settle → release →
  retreat out of the zone. Grasps are chosen by `GraspProbe`: finger yaw across the
  capsule (±23°), wrist lean (0, ±0.3 rad) and finger orientation are collision-checked
  with MuJoCo's own collision detection on a private copy of the model with a 3 mm
  margin around the gripper (a 16 mm opening with a 2 mm margin as a second pass).
  The pre-grasp pose 5 cm above must have a closed-form IK solution too.
- `push_apart(pill, arm)`: when no grasp is clear (touching neighbours, the bottle
  wall), slide the pill 22 mm along its axis, or 0.5 rad off it, with the closed
  fingertips (40 mm when the obstacle is the bottle), wrist upright or leaning
  0.3 rad to either side (next to the bottle only a lean keeps the 84 mm gripper
  housing off it). The start pose, where the fingertips come straight down, must
  be 2 mm clear of everything. Along the sweep the 18 mm fingertip block must stay
  2 mm clear of the bottle and cap but may brush a neighbouring pill by up to 3 mm,
  nudging it aside: the block is twice as wide as a pill, so a side-by-side pair
  cannot be separated otherwise.
- Every transit (approach, transfer to the mouth, retreat, parking) is a Bézier
  path whose control height is raised, up to 15 cm, until the gripper keeps 1 mm
  from the bottle and cap at 11 samples along it; where closed-form IK has no
  solution the controller's least-squares fallback pose is what gets checked. The
  move is then slowed until no joint is asked for more than 1.2–3 rad/s: a lean
  change swings the wrist centre (18.6 cm behind the fingertips), and above these
  rates the servos lag by centimetres and an arm cuts the corner into the bottle.
- Bimanual coordination: each arm works its half of the table. Only one arm at a
  time may use the bottle zone (10 cm around the bottle), and an arm *occupies*
  it whenever the floor projection of its links, or the line from its shoulder
  to its target pill, passes within 12 cm of the bottle — reaching a pill behind
  the bottle puts the forearm over it. A pill within 12 cm of the other arm's
  wrist, fingertips or target is not assigned. Because a planner decides on the
  observation sent with its request (up to seconds old for queued or cloud
  calls), the executive re-checks these rules on the current state when a
  decision arrives and asks again if they no longer hold (`rejected_decisions`).
  An arm with nothing to do parks at rest.
- Safety layer: contact above 10 N between an arm and the bottle, the cap or the
  other arm, or above 60 N between an arm and the table or mat, triggers a
  protective stop — the joints are commanded where they are and the skill backs
  off upward (`protective_stops`), as power-and-force limiting does on a
  collaborative arm. If MuJoCo ever reports a diverging step, the episode ends
  as `simulation_unstable` instead of continuing from the reset state.

## Planner and policy hooks, and the five configurations (`planning.py`, `configs.py`)

- `DecisionPolicy.decide(request) -> (decision, measured_latency_s | None)`: the hook
  for the stand-in planner. The request is JSON (instruction, pills, arms, bottle, motor
  policy, recent results); the decision is
  `{"kind": "skill", "skill_id": "pick_and_drop" | "push_apart", "parameters": {...}}`,
  `wait`, `done` or `decline`. A measured latency replaces the modeled one.
- `PlannerEndpoint`: a FIFO queue and `concurrency` calls in flight: one for the
  edge model (one llama.cpp stream on the Jetson), two for the hosted API (one per
  arm, as a client would issue them). Edge calls finish after the sampled latency;
  cloud calls add a site–cloud round trip (p50 30 ms, p95 90 ms), fail after a 3 s
  connect timeout if the link is down when issued, and after the 12 s request
  timeout if an outage overlaps the response window. Retries back off 1, 2, 4, 8 s.
- `MotorPolicy`: what executes skill calls; a policy that is not available for this
  embodiment returns `policy_unavailable`, and the planner then declines.

| Config (label) | Skill planner (per skill call, closed loop) | Task planner | Motor policy |
|---|---|---|---|
| `edge_qwen_edge_skills` (Edge device planner) | **Real calls** to the model of a connected device's active release (recorded per run) through the device chat API; latency measured per call, no stand-in ([below](#the-edge-device-planner-real-on-device-calls-device_plannerpy)) | – | scripted skills on the robot |
| `cloud_luna_vision` (Cloud GPT-6 Luna (vision)) | **Real calls** to GPT-6 Luna (`gpt-6-luna`, OpenAI Responses API, low reasoning effort) with the head camera image: it points at the next pill in pixels; round trip measured from this machine, both arms' calls can be in flight at once, no stand-in ([below](#the-cloud-vision-planner-real-calls-from-pixels-vision_plannerpy)) | – | scripted IK pick at the pointed table location (`point_skills.py`); no pill pose read |
| `edge_qwen_cloud_astra` (Edge Qwen + GPT Astra) | Edge Qwen2.5-1.5B Q4_K_M on the Jetson Orin Nano, **modeled**: stand-in decisions, p50 150 ms, p95 700 ms (Convoy soak 122/636 ms, deploy smoke 181/900 ms, `control-plane/docs/VERIFICATION.md`) | Cloud GPT-6 Astra (low effort): decomposes the task before the start (waits ≤8 s), re-verifies every 8 placed pills without blocking | scripted skills |
| `cloud_astra_only` (GPT Astra) | Cloud GPT-6 Astra: p50 3.9 s (Artificial Analysis, OpenAI API: median time to first answer token 2.96 s, 43.2 output tokens/s, plus ~40 output tokens), p95 6.0 s assumed (only medians are published) | – | scripted skills |
| `edge_smolvla_cloud_astra` (Edge SmolVLA + GPT Astra) | Cloud GPT-6 Astra | – | SmolVLA-450M on the Jetson: **unavailable** (no checkpoint for this embodiment) |

In the outage slice the hosted planner is unreachable from 15 s to 45 s: Edge Qwen +
GPT Astra keeps placing pills (the task planner's checks just fail), the
cloud-only one holds and retries (the edge device planner has no cloud link, so the slice does
not apply to it; the cloud vision planner's calls go over the real network, so a modelled outage
does not apply either). SmolVLA's configuration dispatches the first
skill, gets `policy_unavailable` for both arms and the planner declines: the
episode ends after ~8 s with no pill placed.

Simulated time is what the robot experiences: a planner call's latency elapses in
simulation while the arms hold, so planning delays and outages cost task time.
Wall-clock compute of the stand-in decision code is recorded separately.

## The edge device planner: real on-device calls (`device_planner.py`)

The edge device planner (`edge_qwen_edge_skills`; the id predates it and is kept
for earlier runs) has no latency model and no stand-in. Every skill decision is a
real request to the model on a connected device, and the episode cannot start
without that connection.

| Part | What it is | Measured or scripted |
|---|---|---|
| Decision: which arm, which pill, which skill, or wait / done | the model of the device's active release, as the platform reports it at run start | real call per decision |
| Scene the model reads | text computed from the simulator state (privileged) | from the simulator |
| Executive: when to ask, parsing, the choice check, the failure policy, the separation re-check | `device_planner.py`, `episode.py` | scripted, fixed before the run |
| Motion | the IK skill library on the simulator state | scripted |
| Time an arm waits for a decision | each call's measured end-to-end round trip | measured |

**Text only.** The planner model reads text: it never sees the camera.
Each request describes the scene in text computed from the simulator: the bottle
position, the free arm and its gripper position, what the other arm is doing,
which pills are already in the bottle, and every pill on the table with its
position in cm, in three lists for the free arm: pills it can pick now, pills it
must push apart before picking (its last pick found no clear grasp), and pills it
cannot take now, with the reason. An arm can take a pill on its own half of the
table plus 2 cm, 18–62 cm from its shoulder, when the executive's separation rules
allow it (not within 12 cm of the other arm's wrist, fingertips or target pill,
and not past the bottle while the other arm uses the bottle zone). A pill is given
up after 4 picks or 2 pushes, or when a skill found no grasp or push pose for it
(the stand-in planner's limits).

**Request.** One worked exchange on a small fixed scene (user request, assistant
reply) and then the real request, as three chat messages (`build_messages`): on
the development seeds the model wrapped most replies in a ``` code block without
the example. `max_tokens` 32; a valid reply is about 21 tokens. Requests are about
1,000–1,150 tokens.

**Reply.** Exactly one JSON object, whitespace around it allowed, and nothing else
(`parse_reply`); nothing is repaired:

```json
{"arm": "L", "skill": "pick_and_drop", "pill": 7}
{"arm": "R", "skill": "push_apart", "pill": 12}
{"arm": "L", "skill": "wait"}
{"arm": "R", "skill": "done"}
```

Text around the object, a code block, a cut-off object, duplicate keys or NaN are
`invalid_json`; a non-object, a missing or extra key, another skill name, an arm
other than "L"/"R" or a pill that is not an integer are `invalid_schema`.
`check_choice` then refuses what the scene in the same request rules out
(`invalid_choice`): `wrong_arm` (not the free arm), `unknown_pill`,
`pill_in_bottle`, `taken_by_other_arm`, `pill_not_on_table`, `out_of_reach`,
`pill_blocked` and `given_up` (from the cannot-take list), `needs_push_apart`
(a pick of a must-push-apart pill), `push_not_needed` (a push of a can-pick pill),
`wait_with_pill_available`, `done_with_pills_on_table`.

**Failure policy** (`FailurePolicy`, fixed before an evaluation and recorded in
its manifest):

- A decision takes at most 3 calls: the first ask and two re-asks, each with a
  fresh scene. After a refused reply the re-ask quotes the reply and the reason it
  was refused; after a device error, an HTTP error or a timeout it is the plain
  request again.
- Without a usable action after 3 calls the decision *fails*: the arm parks and
  asks again after the other arm's next skill result, or 10 s of simulated time.
- An episode makes at most 2 × pills + 12 calls (60 for 24 pills, 72 for 30); then
  it ends as `planner_stopped:planner_call_budget_exhausted`.
- After any call that brings no reply the device is checked. Offline, not
  eligible for chat, a changed model, or a refused session (401) or account (403)
  stops the episode and the evaluation (`planner_stopped:…`; later episodes are
  reported as not run).
- A call with no terminal result after 45 s is a timeout; its elapsed time counts
  like any round trip, and before the next request the client waits (wall clock
  only) until that request has finished or expired, so the device never has two.
- When an accepted action arrives, the executive re-checks the separation rules on
  the current state (the other arm kept moving during the round trip). If its only
  conflict is that the other arm now uses the bottle zone, the action is held until
  the zone is free (at most 6 s, as a pick next to the bottle waits) and checked
  again; any other conflict, or a hold that runs out, rejects it as stale and a new
  decision starts at once. The model's choice is never changed.

**Transport.** `PlatformChatClient` (transport `platform-chat-v1`) uses the
website's public device chat contract with one signed-in operator session:

- `GET /api/platform/chat/devices` lists the physical devices the account sees:
  chat availability, the active release (model repository, revision, file,
  SHA-256, quantization from the GGUF header, runtime, decoding) and the runtime
  the device reports. The client plans on the device named by `--planner-device`,
  or on the only one listed. With several and none named it refuses to start.
- `POST /api/platform/devices/{device}/chat` with `{request_id,
  expected_release_id, messages, max_tokens}` (202), then `GET
  /api/platform/devices/{device}/chat/{request_id}` until `succeeded`, `failed` or
  `expired`. The result carries the device's inference trace id and the release
  id. The control plane relays the request (`/api/v1/devices/{device}/chat`) to the
  agent on the device, which claims requests about once a second and runs them
  through its gateway.

Limits: user and assistant messages only, at most 16 messages and 8 KiB of text,
1–128 output tokens, one request at a time per device; 6 sends and 180 reads per
minute per session (20 and 1,200 per site); requests expire after 120 s. Sends are
spaced by the listed limit (60 s / 6 + 0.5 s = 10.5 s); reads poll every 0.25 s for
5 s, then every second. A 429 is recorded with its `Retry-After`, and the next send
waits that long. One request is outstanding at a time: after a timeout, or a send
whose outcome is uncertain, the next send first reads that request until it is
finished, expired or unknown. A refused session (401) or account (403) stops the
run. The active release is pinned when the run starts; a 409 `release_changed`,
or a device check that finds another release, stops it as `device_model_changed`.
The API takes no temperature or seed: decoding is the active release's.

Runs before this transport used the website portal's relay (`/api/portal/chat` and
`/api/portal/snapshot`). That is a different timing condition: the measured round
trip went through other website routes, so runs on the two transports are not
compared one to one. Their call records have no `transport` field and read as the
legacy portal relay (`portal-relay`, `device_planner.transport_of`). Every record
and export from this transport says `platform-chat-v1`.

**Model.** When the run starts, the client records what the platform reports for
the device (`describe_model`): device and release ids, release name and digest,
model repository, revision, file, SHA-256 and quantization, runtime name, backend
and version, decoding, context and output limits, and the runtime the device
reports (backend, build, GPU layers). A fact the platform does not report is
recorded as "unknown", never inferred from a file name. It goes into
`manifest.json` (`device_planner.model`, with `transport`), each episode's
`summary.json` and the offline evaluation's labels:

- the configuration label, e.g. "Edge: `<repository> <quantization>` · `<runtime>` ·
  `<release>` · Cloud: none";
- the policy label, which names the transport;
- the default evaluation name, which names the model and the transport.

**Time.** Each call blocks the simulation in wall-clock time; then the requesting
arm holds in simulated time for exactly the call's measured end-to-end round trip
(client send to terminal result), rounded up to the next 10 ms control tick, while
the other arm keeps working. That round trip includes the website, the control
plane relay, the agent's one-second claim interval and the client's polling; the
on-device latency (gateway slot to completion), first-token time and queue time
are recorded beside it. A robot calling its own Jetson directly would wait about
the on-device latency, so these episodes are pessimistic about planning time.
The pacing wait for the routes' rate limit is wall-clock only and not counted.

**Audit.** Every call is a line in the episode's `planner_calls.jsonl` (and in
`OUTPUT/<config>/calls.jsonl`): the transport, the messages sent, the raw reply,
the platform trace id (the device's inference trace, listed in its Traces), the
release id, request id, send and finish times, HTTP and relay status (with a
429's `Retry-After`), on-device latency, first-token and queue time, tokens in and
out, the end-to-end round trip, the parse result and refusal reason, the action,
what followed (delivered, re-asked, failed decision, stale rejection, stopped) and
the simulated start and end times. No credentials.

**Decisions.** A decision is one action for one free arm. It is *resolved* when a
call returns an accepted action (a valid reply) or when it fails after its three
calls; a decision still open when the episode ends is not counted. Each episode
exports, from its call records:

- `planner_decisions`: resolved decisions (= valid replies + failed decisions);
- `planner_first_call_accepted`: resolved decisions whose first call was accepted;
- `planner_reasked_decisions`: resolved decisions that needed at least one re-ask,
  accepted after it or failed;
- `planner_failed_decisions`: no usable action after three calls.

So decisions = first-call accepted + re-asked, and accepted after a re-ask =
re-asked − failed. Configurations shows "Accepted on the first call" as
Σ first-call accepted ÷ Σ decisions. Earlier evaluations do not report these
counts and show it as Not reported.

## The cloud vision planner: real calls from pixels (`vision_planner.py`)

The cloud vision configuration (`cloud_luna_vision`, shown as "Cloud GPT-6 Luna
(vision)") has no stand-in and no text scene. Every skill decision is a call to
GPT-6 Luna (`gpt-6-luna`, OpenAI, low reasoning effort) with the rendered head
camera image, made from the machine that runs the simulation. The model points at
the next pill in pixels; nothing it is given comes from the simulator's state.

| Part | What it is | Measured or scripted |
|---|---|---|
| Decision: which pixel to pick at, or wait / done, for the free arm | GPT-6 Luna through the OpenAI Responses API | real call per decision |
| What the model sees | the head camera image (RGB) and a short instruction | rendered from the simulation |
| Pixel to table point | back-projection with the same camera's depth, intrinsics and pose (`head_camera.py`) | scripted, from pixels |
| Grasp yaw | across the long axis of the raised depth blob under the pixel, turned until the fingertips come down on clear mat (`choose_finger_yaw`) | scripted, from pixels |
| Executive: parsing, the pixel check, the failure policy, the two-arm re-check | `vision_planner.py`, `vision_episode.py` | scripted, fixed before the run |
| Motion: reach, descend, close, lift, transfer, release | the IK skill library (`point_skills.PickAtPoint`); transits are checked against the bottle and cap poses | scripted |
| Time an arm waits for a decision | each call's measured round trip | measured |

**Camera.** The head camera (the RGB-D bar on the head, 64° vertical field of view)
is rendered at 1024 × 768, colour and depth from the same pose at the same instant.
Depth is MuJoCo's depth buffer in metres along the optical axis. The planner gets
columns 256–767 and rows 208–591 of that render (512 × 384), the mat in front of the
bottle at full resolution: a 2× digital zoom, about 1 mm per pixel across the mat. A
pill is about 8 × 20 px there. Yellow ticks on the image borders every 32 px
(labelled every 64) help the model read coordinates. Nothing about the scene is
drawn: no ids and no positions. The image is a JPEG (quality 92) sent as an
`input_image` data URL with `detail: high`.

**Request.** One user message: the instruction, then the image (`build_content`).
The instruction (`luna-vision-v3`) names the task, the bottle and cap, which arm
is free and the image column where each arm's reach ends (from the calibration).
It also gives what the other arm is doing, from the executive's own commands
(idle, carrying, or picking at a pixel: choose a pill at least 120 px from it), and
the free arm's last pick, from its gripper and wrist force sensing (e.g. "the
fingers closed on nothing at (212, 140)"). Spots where two picks failed are
listed. The instruction asks for a pill with free mat around it, and for a
touching or bottle-side pill when no free one is left. Wait is only for when the
other arm is near every pill the free arm could take. Done is only for when no
pill is left on the arm's side (look along the edges and around the bottle).
Settings: `reasoning: {effort: "low"}`, `max_output_tokens` 2048 (reasoning
included), `store: false`, and the reply schema as a strict structured-output
format.

**Reply.** Exactly one JSON object (`parse_reply`), nothing repaired:

```json
{"arm": "L", "action": "pick", "u": 212, "v": 140}
{"arm": "R", "action": "wait", "u": null, "v": null}
{"arm": "L", "action": "done", "u": null, "v": null}
```

A refusal by the model, an empty or cut-off reply, a code block or any other shape
is refused (`invalid_json`, `invalid_schema`, `model_refusal`). `check_point` then
refuses a pick the executive cannot act on (`invalid_choice`). It reads the frame's
depth and the robot's own geometry only:

- `outside_image`, or `no_depth`;
- `not_on_mat`: the pixel sees something more than 12 mm above the mat (the
  bottle, the cap, an arm);
- `out_of_reach`: the device planner's reach rule on the back-projected point;
- `given_up`: within 10 mm of a spot where two picks already failed;
- `next_to_other_arm`: within 12 cm of the other arm's wrist, fingertips or the
  point it is picking at;
- `wrong_arm`.

Wait and done are accepted as given: whether pills are left is not something the
executive knows without the model. Done ends that arm's work. The episode ends
when both arms are done.

**From pixel to grasp.** The accepted pixel is back-projected with its depth to
the visible surface (the top of a pill is about 3 mm nearer the camera than its
centre at this viewing angle), and `PickAtPoint` goes to that table point. It never
moves to the nearest pill, so pointing at bare mat closes the gripper on nothing.

1. The fingers close across the long axis of the raised depth blob under the
   pixel, the depth points 2–14 mm above the mat connected to it.
2. If a raised point lies where an open fingertip would come down, the yaw turns by
   up to 0.6 rad to the first clear one.
3. The grasp height assumes a pill lying flat on the mat (a fixture of the
   station), and the grasp pose must have a closed-form IK solution and clear the
   bottle and cap.
4. A finger that lands on something stops the descent at 2 N (`blocked`).
5. Fingers that close past 5.5 mm hold nothing (`empty_grasp`), and fingers that
   close during the lift dropped the pill (`dropped`).

After a release the arm retreats only out of the bottle zone, as `PickAndDrop`
does, and asks for its next pick from there. That pose is in the camera's view,
beside the bottle, so the arm's own gripper can hide pills from its next frame. A
failed pick therefore parks the arm at rest, outside the window, before it asks
again. A wait or done said away from rest is not acted on: the arm parks and asks
again from rest, and only a wait or done said from rest counts. In development an
arm said done from beside the bottle while its gripper hid the last pill on its
side. Retreating to rest after every pick also fixed that, but cost about 1 s per
pick.

Once the arm holds a pill, the spot it picked at stops counting as where it works
for the bottle-zone rule (its links do), as `PickAndDrop`'s target pill moves with
the gripper. Without that, two arms lifting at once deadlocked in development.
Whether a pill was lifted, and whether it ended in the bottle, is measured on the
simulator after the fact, for the metrics only.

**Failure policy** (`VisionFailurePolicy`, fixed before the evaluation and recorded
in its manifest):

- A decision takes at most 3 calls: the first ask and two re-asks, each with a
  fresh frame. After a refused reply the re-ask quotes the reply and the reason;
  after an HTTP or transport error, an API-side failure or a timeout it is the
  plain request again.
- Without a usable action after 3 calls the decision fails: the arm parks and asks
  again after the other arm's next result, or 10 s of simulated time.
- An episode makes at most 3 × pills + 12 calls (84 for 24 pills, 102 for 30).
- The spend cap refusing the next call, or the API refusing the key or the model
  (401, 403, 404), stops the episode and the evaluation.
- A call without a response after 60 s is a timeout and costs its whole wait in
  simulated time.
- An accepted pick is re-checked against the two-arm rules when it arrives, as
  the device planner's are: held for the bottle zone (at most 6 s) or rejected as
  stale.

**Transport, time and spend.** `OpenAIResponsesClient` (transport "openai-responses
from cloud container") sends `POST https://api.openai.com/v1/responses` and
measures each call from the request sent to the response received. The arm holds
in simulated time for exactly that round trip (to the next 10 ms tick), while the
other arm keeps working. Both arms can have a call in flight at once, as with any
hosted API.

The key is read from `OPEN_AI_API_KEY` and kept in the request header only: never
printed, logged or written. Every call is priced from its `usage`: $0.10 per 1M
input tokens, $0.01 per 1M cached input and $0.50 per 1M output, reasoning
included. The cost goes to a spend ledger (`--spend-ledger`, JSON lines). Before
each call the ledger books the call's worst case: its input estimate, all
uncached, plus the whole output cap. It refuses the call when recorded spend plus
open bookings plus that worst case would pass the cap (`--spend-cap-usd`, default
3.00). A timeout or a broken connection is booked at its worst case, since the API
may have run it; an HTTP error is not billed.

**Audit.** Every call is a line in the episode's `planner_calls.jsonl` and in
`OUTPUT/<config>/calls.jsonl`, with no key. A line holds the instruction, the image
digest and file (every image sent is kept in the episode's `planner_images/`, not
uploaded), the response and request ids, the model the API reported, the status,
the round trip, the API's processing time (`openai-processing-ms`), the tokens
(input, cached, output, reasoning) and the cost. It also holds the raw reply, the
parse result and the refusal reason, the target (pixel, table point, finger yaw,
whether an axis was seen, the obstruction count), what followed, and the simulated
start and end. `summary.json` lists every pick with its outcome.

**Measured first call.** One call on a real rendered frame (dev seed 1000, t = 0,
the first prompt version) came before anything else. It used 599 input tokens: 231
for the 512 × 384 image and 368 for the text and schema, as the API's input-token
counter splits them. It used 138 output tokens, 111 of them reasoning, and took
4.48 s end to end (3.2 s on the API side) for $0.000129. The reply pointed within
1 px of a pill's centre. The frozen prompt is longer: about 710–750 input tokens
per call, the image still 231 of them.

## Evaluation (`evaluate.py`)

Slices: `nominal` (24 pills, 0.20 × 0.44 m), `pill_count_30`, `scatter_wide`
(0.26 × 0.54 m), `scatter_tight` (0.11 × 0.17 m), `lighting_dim` (frames only: the
skills read state), `pill_near_bottle` (4 pills 4–16 mm from the wall),
`network_outage` (site–cloud link down from t = 15 s to 45 s).

Episodes go to `OUTPUT/<config>/<seed>-<slice>/`. With `--seed-stride N` the i-th
slice runs seeds `i·N + seed`, so every episode of a configuration has its own
seed (Convoy groups an offline evaluation's rollouts by seed), and every
configuration runs the same layouts: a layout depends only on the slice and seed.

### Cloud GPT-6 Luna (vision): real calls from pixels

2026-10-02, 73 min of wall time on a Linux cloud container. Every decision was a real
call to `gpt-6-luna` (the API reported this model for every call) through the OpenAI
Responses API at low reasoning effort, measured from the container. The run used
commit `113e19b` (clean tree): prompt `luna-vision-v3`, the failure policy and
the settings above, frozen and committed before the run. Physics as above (2 ms,
noslip 4), 150 s horizon. The seeds were the Jetson run's: nominal 0–4 and 30-pill
200–204 (`--seed-stride 200`). Prompt and executive work used only the development
seeds 1000, 1001 and 1100. Imported as the offline evaluation "Pills to bottle ·
Cloud GPT-6 Luna (vision) · gpt-6-luna · openai-responses from cloud container".

| Slice | Seed | Pills placed | Outcome | Calls | Refused | Failed decisions | Picks | Empty grasps | Blocked descents | e2e p50 / p95 (ms) | Tokens in / out | Cost (USD) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| nominal | 0 | 21/24 | both arms done at 141.3 s | 31 | 1 | 0 | 23 | 0 | 2 | 3,872 / 7,289 | 22,369 / 7,831 | 0.0062 |
| nominal | 1 | 24/24 | all in at 125.6 s | 29 | 0 | 0 | 23 | 0 | 0 | 3,497 / 6,277 | 20,673 / 7,072 | 0.0056 |
| nominal | 2 | 23/24 | both arms done at 130.4 s | 32 | 0 | 0 | 23 | 0 | 0 | 3,532 / 6,011 | 22,871 / 7,328 | 0.0060 |
| nominal | 3 | 20/24 | horizon | 33 | 3 | 0 | 22 | 0 | 2 | 4,159 / 9,362 | 23,786 / 10,962 | 0.0079 |
| nominal | 4 | 21/24 | horizon | 33 | 3 | 0 | 25 | 0 | 4 | 3,843 / 8,410 | 23,864 / 10,056 | 0.0074 |
| 30 pills | 200 | 26/30 | horizon | 30 | 0 | 0 | 26 | 0 | 0 | 4,149 / 6,114 | 21,371 / 8,532 | 0.0064 |
| 30 pills | 201 | 27/30 | horizon | 32 | 0 | 0 | 27 | 0 | 0 | 3,812 / 7,309 | 22,770 / 8,999 | 0.0068 |
| 30 pills | 202 | 24/30 | horizon | 31 | 0 | 0 | 28 | 0 | 4 | 4,081 / 7,664 | 22,170 / 9,355 | 0.0069 |
| 30 pills | 203 | 24/30 | horizon | 30 | 1 | 0 | 25 | 0 | 1 | 3,895 / 6,081 | 21,388 / 8,916 | 0.0066 |
| 30 pills | 204 | 26/30 | horizon | 33 | 0 | 0 | 28 | 0 | 0 | 4,141 / 8,061 | 23,786 / 10,908 | 0.0078 |

**Nominal (seeds 0–4).**

- **Pills.** 1 of 5 episodes put every pill in, and 109 of 120 pills were placed
  (91%).
- **Decisions.** 158 calls made 151 decisions: 144 accepted on the first call (95%)
  and 0 failed decisions.
- **Refused replies.** 7, all on the pixel check: next to the other arm 4, given up
  2, out of reach 1. No reply was malformed, no call failed and none was refused by
  the model.
- **Grasps.** 116 picks: 0 grasps on empty space and 8 descents stopped on a
  neighbouring pill. 108 pills were lifted; the 109th was still in the arm's retreat
  when seed 1 ended as a success.
- **Latency.** End to end p50 3,827 ms and p95 7,772 ms over the slice's 158
  answered calls; the median of the episodes' own p50 and p95 is 3,843 and 7,289 ms.
- **Tokens and cost.** 113,563 in and 43,249 out (reasoning included), $0.0330, or
  $0.0066 per episode.

**30 pills (seeds 200–204).**

- **Pills.** 0 of 5 episodes put every pill in, and 127 of 150 pills were placed
  (85%). Every episode reached the 150 s horizon.
- **Decisions.** 156 calls made 155 decisions: 154 accepted on the first call (99%)
  and 0 failed decisions.
- **Refused replies.** 1 (next to the other arm).
- **Grasps.** 134 picks: 0 grasps on empty space, 5 blocked descents and 2 picks
  with no clear grasp beside the bottle. 127 pills were lifted.
- **Latency.** End to end p50 4,064 ms and p95 7,283 ms over 156 answered calls.
- **Tokens and cost.** 111,485 in and 46,710 out, $0.0345, or $0.0069 per episode.

**Both slices.**

- **Calls.** All 314 calls returned `completed` within the 2,048-token output cap:
  no HTTP or transport errors and no timeouts. The longest round trip was 10.8 s.
- **Tokens per call.** About 710–750 input tokens: 231 for the image and the rest
  for the instruction and schema. About 230 output tokens at p50, roughly 90% of
  them reasoning.
- **Safety.** No arm–arm contact, no protective stop and no pill lost. The peak
  bottle tilt was 0.19°, and no step was unstable.
- **Why episodes ended short.**
  - Every closed grasp lifted a pill, and every lifted pill went into the bottle.
    The pills left behind were either out of time (the horizon) or ones the
    executive gave up after two blocked picks: pills touching each other, or
    standing against the bottle.
  - In nominal seed 2 the last pill lay 6.3 cm behind the bottle, where the
    bottle itself hides it from the head camera. Both arms said done from rest.
    Replaying the recorded calls reproduces the episode exactly and puts the pill
    there.
- **Compared with the Jetson run.** On the same layouts, the Jetson run below
  placed 114/120 (nominal) and 119/150 (30 pills) from a text scene computed from
  the simulator state. That is a different condition (privileged state, serial
  device calls through the legacy relay), so the runs are not compared one to one.
- **Spend.** The whole task cost $0.1482 for 676 calls: the measured first call,
  361 development calls and the evaluation's 314 calls ($0.0675). The cap was
  $3.00.

In Configurations, the demo workspace's "Cloud GPT-6 Luna (vision)" configuration
(status Testing) shows this evaluation through its "Pill-task sim" simulator robot:

- Edge: none, with the Jetson Orin Nano Super as a thin edge. Cloud: GPT-6 Luna as
  the planner, served through the OpenAI Responses API.
- The robot spec is the other configurations' with the `bimanual-station` preview.
- The declared provenance reads: MuJoCo planner run · Head camera RGB-D → model
  points in pixels · Scripted IK to the pointed location · GPT-6 Luna (OpenAI, low
  effort), real calls · Runner: Linux cloud container · Transport: OpenAI Responses
  API.
- The eval page's call-result panel shows refused and failed calls as Not reported.
  The website still reads the older per-result metric names, not the merged
  `planner_invalid_format` and `planner_call_failures` (also true of device-planner
  evals since `platform-chat-v1`).

Development, on the development seeds only (prompt and executive changes, each
round on seeds 1000, 1001 and 1100):

- `luna-vision-v1`, seed 1000: 19/24. All 19 closed grasps placed. 7 descents
  were blocked, 5 of them at one spot the model kept choosing while its own arm hid
  it.
- `v2` added the depth-based finger yaw, the given-up rule and parking after a
  failed pick: 18/24, 20/24, 19/30. Blocked descents fell to 0–3 per episode, but
  the model answered wait for pills next to the bottle.
- `v3` changed the wait and done wording: 21/24 and 23/30. Seed 1000 deadlocked on
  the bottle zone (fixed, with a regression test).
- Retreating to rest after every pick gave 20/24 (that run ended at 114.7 s on a
  MuJoCo divergence after a blocked descent), 22/24 and 22/30. It cost about 23 s
  per episode in an oracle check, so it was replaced by asking wait and done again
  from rest.
- That frozen version placed 24/24, 22/24 and 22/30.

After the run, a pick's `placed` outcome was tightened to the pills it lifted. The
frozen code counted any pill that entered the bottle during the pick. In this
evaluation every pick reported as placed had lifted exactly one pill, and those
pills account for every placed pill, so no reported number changes.

### Earlier real run on a Jetson (legacy portal relay)

This run used the transport before `platform-chat-v1`: the website portal's relay
(`/api/portal/chat`), a different timing condition. Its records carry no transport
or decision-level counts, and its model is the one the device then reported.

2026-10-02, 76 min of wall time. Every decision was a real call to the connected
Jetson Orin Nano: release `rel_7horo87k6lxs`, Qwen2.5-1.5B-Instruct Q4_K_M,
llama.cpp CUDA, `--temp 0.0 --seed 42` as the device reported. Calls went through
the portal relay. Physics as above (2 ms, noslip 4), 150 s
horizon, nominal seeds 0–4 and 30-pill seeds 200–204. The prompt (`pill-planner-v5`),
the 32-token cap and the failure policy were fixed before the run; prompt work
used development seeds 1000, 1001 and 1100 only.

| Slice | Seed | Pills placed | Outcome | Calls | Valid | Refused choices | Failed decisions | Protective stops | e2e p50 (ms) | On-device p50 (ms) |
|---|---|---|---|---|---|---|---|---|---|---|
| nominal | 0 | 18/24 | horizon | 39 | 33 | 6 | 1 | 8 | 2,675 | 1,345 |
| nominal | 1 | 24/24 | all in at 143.1 s | 39 | 30 | 9 | 2 | 0 | 2,673 | 1,333 |
| nominal | 2 | 24/24 | all in at 145.1 s | 36 | 25 | 11 | 3 | 0 | 2,654 | 1,330 |
| nominal | 3 | 24/24 | all in at 136.8 s | 35 | 26 | 9 | 2 | 0 | 2,671 | 1,328 |
| nominal | 4 | 24/24 | all in at 133.9 s | 36 | 26 | 10 | 1 | 0 | 2,692 | 1,337 |
| 30 pills | 200 | 22/30 | horizon | 40 | 26 | 14 | 2 | 0 | 2,801 | 1,390 |
| 30 pills | 201 | 24/30 | horizon | 27 | 27 | 0 | 0 | 0 | 2,738 | 1,406 |
| 30 pills | 202 | 26/30 | horizon | 46 | 41 | 5 | 0 | 0 | 2,658 | 1,391 |
| 30 pills | 203 | 22/30 | horizon | 40 | 25 | 15 | 4 | 0 | 2,722 | 1,380 |
| 30 pills | 204 | 25/30 | horizon | 40 | 34 | 6 | 0 | 0 | 2,719 | 1,398 |

- **Episodes.** 4 of 10 put every pill in the bottle, all of them nominal: 114 of 120 nominal pills (95%) and 119 of 150 in the 30-pill slice (79%). Serial decisions of about 2.7 s use up the 30-pill horizon (6 s per pill for 25 pills) first.
- **Replies.** 378 calls: 293 valid, 85 refused choices (out of reach 48, cannot-take list 18, needs a push first 12, already in the bottle 5, taken by the other arm 1, wait with a pill available 1). There were 0 invalid JSON or schema replies, 0 device or HTTP errors and 0 timeouts. 15 decisions failed after 3 calls. Every reply ended with `stop` within the 32-token cap.
- **Bottle zone.** Accepted actions were held 47 times for the bottle zone (at most 2.4 s); none went stale.
- **Latency.** End-to-end p50 2,697 ms and p95 3,453 ms (this drives simulated time). On-device p50 1,367 ms and p95 1,452 ms; first token p50 808 ms; about 906 tokens in and 22 out.
- **Relay delays.** Three calls took 5.6, 24.0 and 25.0 s end to end with normal on-device latency: delays in the relay, not in the model.
- **Device counter.** The device's gateway counter went from 224 to 602 requests: every call was served, and nothing else used the device.
- **Safety.**
  - In nominal seed 0 the arms collided at t = 99 s: the right arm was approaching pill 17 while the left arm lifted pill 13 toward the bottle. The peak contact was 1,115 N, followed by 140 contact steps and 8 protective stops.
  - Both choices had passed the separation checks when made and again on delivery. The scripted two-arm coordination, which the stand-in's ordering rarely tested, does not prevent every crossing.
  - No other episode had arm contact. No pill was lost, and the peak bottle tilt was 0.24°.

### Modelled demo results (stand-in planner)

`scripts/pill_task_eval.sh` (seeds 0–2, stride 100), every configuration on the
same 9 layouts (MuJoCo 3.3.0, 2 ms, 150 s horizon; 35 min on 4 CPU workers). These
are the stand-in configurations: their planner decisions are the rule-based
stand-in and their latency is modeled. The `edge_qwen_edge_skills` row this matrix
once had (the stand-in with a latency model) is withdrawn: that configuration now
calls the real model, and its results are
[the real run](#earlier-real-run-on-a-jetson-legacy-portal-relay).

| Configuration | nominal (0–2) | cloud outage 15–45 s (100–102) | 30 pills (200–202) | Episodes all placed | Pills placed | Median time to all placed (s) | Median planner wait (s) |
|---|---|---|---|---|---|---|---|
| Edge Qwen + GPT Astra | 3/3 · 72/72 | 3/3 · 72/72 | 3/3 · 90/90 | 9/9 | 234/234 (100%) | 95.9 | 14.0 |
| GPT Astra | 2/3 · 69/72 | 0/3 · 65/72 | 0/3 · 75/90 | 2/9 | 209/234 (89%) | 141.2 | 144.2 |
| Edge SmolVLA + GPT Astra | 0/3 · 0/72 | 0/3 · 0/72 | 0/3 · 0/90 | 0/9 | 0/234 (0%) | – | 14.3 |

Cells: episodes with every pill in the bottle / episodes · pills placed / pills.
Planner wait is summed over both arms (an arm waits from its request to the answer).

- **Edge Qwen + GPT Astra** placed every pill in all 9 episodes; it starts about
  4 s late while the task plan arrives, and in the outage its 1 verification call
  per episode fails and nothing waits on it. In one 30-pill episode the arms touched twice (peak
  35 N); both contacts triggered protective stops, and the arms backed off and
  finished.
- **GPT Astra** waits ~4 s per skill decision (p50 3.8–4.5 s per episode), so an
  arm spends about half the episode waiting: it finished 2 of 3 nominal episodes
  at 139 s and 144 s and ran out of time in the rest. During the outage both arms
  hold for 30 s (8–9 failed calls, 3–4 operator pages per episode).
- **Edge SmolVLA + GPT Astra** places nothing: both arms' first skill calls come
  back `policy_unavailable`, the planner declines, and the episode ends after
  6–8 s. This is the honest result for a policy that was never trained on this
  robot; nothing stands in for it.
- Physics and safety across the matrix (including the withdrawn row): 656
  completed transfers placed their pill and none dropped one; no pill left the
  table; peak bottle tilt 1.4°; no unstable step.
- Export: 6,491 frames (5,899 with an image; repeated frames carry none), 41.7 MB
  of JPEG in total, 5 KiB to 2.0 MB per episode; every configuration directory
  passes `import_offline_eval.py --dry-run`.

`evaluate` also writes these tables (plus planner calls, latency and safety
counts) to `results.md` and per-episode `summary.json` files.

## Offline replay export (`offline_replay.py`)

`--replay offline` records each episode in the format
`scripts/import_offline_eval.py` uploads ([offline evaluations](../../../docs/v1/offline-evaluations.md));
`evaluate` also writes `evaluation.json` (name, task, configuration and policy
labels) in each configuration's directory, so one import per configuration
creates one offline evaluation, tagged "Offline sim" in Configurations.

| File | Content |
|---|---|
| `replay.json` | `steps`, `seed`, `outcome` (`success` all pills in, `timeout` at the horizon, else `failure`), `metrics`, `action_labels`, `skill`, `planner_ms` (median modeled skill-planner latency), `sim_seconds` |
| `frames/NNNN.json` | `index`, `action`, `reward`, `success`, `policy_ms`; frame 0 has the image only |
| `frames/NNNN.jpg` | the head camera, 256 × 256 baseline JPEG (rendered at 512, averaged down; ~5–9 KiB) |
| `summary.json` | the full episode summary, with the export's frame and byte counts (not uploaded) |

- One step is 0.5 s of simulated time, about one skill phase; at most 299 steps,
  so an episode has at most 300 frames (over the 150 s horizon the last step runs
  to the end).
- A frame within 8 levels of the last stored image in every pixel (both arms
  holding for a planner) carries no image, and the player repeats the previous one.
- `action` is 8 values, `L x, L y, L z, L grip, R x, R y, R z, R grip`: each arm's
  mean commanded TCP velocity over the step divided by the 0.6 m/s skill limit,
  and its gripper command (−1 open 80 mm, +1 closed). `reward` is the fraction of
  pills in the bottle, `success` whether all are, `policy_ms` the measured wall
  time of the skill controller during the step.
- `wall_seconds` is left out: episode times are simulated, and Convoy then shows
  simulated time. The simulator's own wall time is the `sim_compute_wall_s` metric.
- `metrics` (28 per episode): pills total/placed, fraction placed, time to all
  placed, planner calls, failures, p50/p95 and total wait, skill calls, picks
  placed and dropped, grasp failures, pushes, refused skills, protective stops,
  operator pages, arm–arm contacts, peak contact force, peak bottle tilt, pills
  lost, stale decisions rejected, outage seconds, motor policy availability, and
  the configuration, slice and end reason as text. `picks_placed` counts finished
  pick-and-drop calls: the one that drops the last pill is still retreating when
  the episode ends, so on success it is one less than `pills_placed`.
- `--preview-camera photo` (with `--previews` to pick episodes) also writes
  `preview.mp4` and `preview.gif`: the wide view beside the head camera with a
  caption. They are not uploaded.
- Device-planner episodes export measured numbers instead. `planner_ms` is the
  median end-to-end round trip of the calls the model answered. `skill` lists the
  skills it chose and the executive started (e.g. `pick_and_drop ×30, push_apart
  ×3`). The 32 metrics (the import's cap) are:
  - the calls by result: valid, `planner_invalid_format` (invalid JSON or schema),
    invalid choice and `planner_call_failures` (device errors or expiries, HTTP
    or connection errors, client timeouts), which add up to `planner_calls`;
  - the decision counts above;
  - stale rejections, end-to-end and on-device p50/p95, first-token p50, tokens in
    and out p50 and planner wait;
  - the task and safety counts.

  Outage seconds and motor-policy availability do not apply and are left out.
- Cloud vision planner episodes (`vision_metrics`) export 32 metrics:
  - the calls by result, as above; `planner_invalid_format` also counts model
    refusals and `planner_call_failures` API-side failures;
  - the decision counts, stale rejections, and the end-to-end p50/p95;
  - tokens: in and out p50 per call, and in and out (reasoning included) summed
    over the episode, with `planner_cost_usd`, the episode's cost;
  - the grasp outcomes: `grasp_attempts` (picks started), `grasps_empty` (the
    fingers closed on nothing), `grasps_blocked` (the descent was stopped by
    contact), `pills_grasped` (pills lifted off the mat) and `picks_placed`;
  - pills total, placed, fraction and time to all placed, protective stops,
    arm–arm contacts, peak bottle tilt, slice and end reason;
  - `measurement_source`, which names the model the API reported, the provider,
    the effort and the transport ("Measurement source" on the eval page).

  `skill` lists the chosen actions (e.g. `pick_at_point ×31, wait ×2`) and
  `planner_ms` is the median round trip. The evaluation's labels name the model,
  provider, effort and transport (`configs.vision_labels`).
  Exports before `platform-chat-v1` had five separate counts instead of the two
  merged ones (`planner_invalid_json`, `planner_invalid_schema`,
  `planner_device_errors`, `planner_http_errors`, `planner_timeouts`); the
  merge makes room for the decision counts. The per-result counts stay in
  `calls.jsonl` and `summary.json`.
- The import's frame shape (`index, image_png_base64, action, reward, success,
  policy_ms`) has no planner fields, and the replay page shows `skill` and
  `planner_ms` once per episode. The calls that completed during a step are kept in
  the local `frames/NNNN.json` under `planner` (trace id, arm, action, round trip,
  on-device latency, tokens), with each arm's active skill under `skills`; the
  import script does not send them. Showing them per step on the website would
  need a platform change (frame fields in the API and the replay readout).

## Hosted journal format (`recording.py`)

An episode directory holds `journal.sqlite3` (the coordinator journal schema of
`control-plane/agent/convoy_agent/coordinator/journal.py`), `episode.json` (the
episode record the control plane keeps: id, mission id, release digest, identity,
state, summary), `release.json` (the immutable configuration whose SHA-256 is the
release digest), `summary.json`, `preview.gif` and three key frames.

- `missions`: one row; `report_json` = `{identity, state, detail, summary}`, with
  `summary.steps`, `simulated_duration_s`, `wall_duration_s` and
  `planner_result.{decision, planner_duration_ms}` as the replay header reads them.
- `commands`: one row per 0.2 s step, `state='applied'`.
  `request_json` = `{identity, request_id, observation_id, sequence, observation,
  deadline_monotonic_ns, budget_ms}`; `result_json` = `{identity, request_id,
  observation_id, sequence, deadline_monotonic_ns, action, policy_duration_ms}`
  plus two additive keys the reader ignores, `planner_duration_ms` (modeled latency
  of planner calls completed in the step) and `bimanual` (both arms' 4-value
  commands, active skills and phases, 12 joint targets, planner calls, skill events,
  link state); `observation_json` = `{observation, reward, success, terminated,
  truncated}` like the coordinator's `StepResult`.
- `observation` = `{image_png_base64, state, instruction}`: the 480 × 480 head camera
  as a non-interlaced RGB8 PNG, 14 joint/gripper values, and the instruction. Each
  frame is also the next step's request observation, as the reader's continuity
  check requires. Frames stay at 8 bits per channel unless the journal would exceed
  28 MiB, in which case the largest depth that fits (down to 4 bits) is written.
- `action` is the arm moving more during the step: commanded TCP velocity divided
  by the 0.6 m/s skill limit (x, y, z) and the gripper command (−1 open 80 mm,
  +1 closed), so the replay page's X/Y/Z/Grip readout stays meaningful.
- `policy_duration_ms` is measured wall time of the skill controller (skill
  update and IK for both arms) during the step.

`convoy-sim-pills verify DIR` checks every row against the reader's rules and,
with the `managed` extra, runs `convoy_server.services.replay.manifest` and
`frame` on the file; the tests do the same.

To show an episode in the workspace, the control plane needs an Episode row with
the same identity and summary (it is created when a mission runs). The journal's
rows can then be served as a mounted journal (`CONVOY_REPLAY_JOURNAL`) or uploaded
command by command through the existing
`/api/agent/v1/robots/{robot}/missions/{mission}/recording/commands/{sequence}` and
`/publish` endpoints. Running this task as a platform mission needs a new execution
profile (this robot's observation and action contract, multi-call planning) in
`convoy_contracts`; that is not part of this change.
