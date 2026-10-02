# Bimanual pill task (`convoy_sim.bimanual_pill_task`)

A MuJoCo simulation of a wheeled bimanual station robot putting 20–30 capsule pills
into an open supplement bottle, four planner/policy deployment configurations,
seeds × slices evaluation, and recordings in the platform's replay formats: the
offline evaluation import (`scripts/import_offline_eval.py`) and the hosted
coordinator journal.

Scope, stated plainly:

- **Physics, kinematics and contacts are simulated** with MuJoCo 3.3.0 at SI units.
- **Skills read simulator state** (pill and bottle poses). They are a scripted,
  privileged-state skill library like the MetaWorld scripted reference in this
  package, not perception or a learned policy.
- **Edge Qwen's planner decisions are real model calls.** Every skill decision of
  the Edge Qwen configuration is a request to Qwen2.5-1.5B-Instruct (Q4_K_M,
  llama.cpp CUDA) on a connected Jetson Orin Nano through Convoy's device chat API;
  its latency is measured, not modeled, and nothing stands in when a call fails
  ([below](#edge-qwen-the-real-on-device-planner-device_plannerpy)). Qwen2.5-1.5B
  reads text only, so it gets the scene as text computed from the simulator state,
  not camera images.
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

# Edge Qwen: real calls to the model on the connected device (an operator session on the website):
CONVOY_SERVER=https://deployconvoy.com CONVOY_SESSION_FILE=~/.convoy-session \
MUJOCO_GL=glfw xvfb-run -a uv run --frozen convoy-sim-pills evaluate --configs edge_qwen_edge_skills \
    --slices nominal,pill_count_30 --seeds 0-4 --seed-stride 200 --record all --replay offline \
    --name "Pills to bottle · Edge Qwen · real on-device planner (Jetson)" --output runs/edge-qwen-real
```

Every command writes to a new directory and refuses to overwrite a recording.
`scripts/pill_task_eval.sh` runs the modelled demo matrix (not Edge Qwen).

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

## Planner and policy hooks, and the four configurations (`planning.py`, `configs.py`)

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
| `edge_qwen_edge_skills` (Edge Qwen) | **Real calls** to Qwen2.5-1.5B-Instruct Q4_K_M on a connected Jetson Orin Nano through the device chat API; latency measured per call, no stand-in ([below](#edge-qwen-the-real-on-device-planner-device_plannerpy)) | – | scripted skills on the robot |
| `edge_qwen_cloud_astra` (Edge Qwen + GPT Astra) | Edge Qwen2.5-1.5B Q4_K_M on the Jetson Orin Nano, **modeled**: stand-in decisions, p50 150 ms, p95 700 ms (Convoy soak 122/636 ms, deploy smoke 181/900 ms, `control-plane/docs/VERIFICATION.md`) | Cloud GPT-6 Astra (low effort): decomposes the task before the start (waits ≤8 s), re-verifies every 8 placed pills without blocking | scripted skills |
| `cloud_astra_only` (GPT Astra) | Cloud GPT-6 Astra: p50 3.9 s (Artificial Analysis, OpenAI API: median time to first answer token 2.96 s, 43.2 output tokens/s, plus ~40 output tokens), p95 6.0 s assumed (only medians are published) | – | scripted skills |
| `edge_smolvla_cloud_astra` (Edge SmolVLA + GPT Astra) | Cloud GPT-6 Astra | – | SmolVLA-450M on the Jetson: **unavailable** (no checkpoint for this embodiment) |

In the outage slice the hosted planner is unreachable from 15 s to 45 s: Edge Qwen +
GPT Astra keeps placing pills (the task planner's checks just fail), the
cloud-only one holds and retries (Edge Qwen has no cloud link, so the slice does
not apply to it). SmolVLA's configuration dispatches the first
skill, gets `policy_unavailable` for both arms and the planner declines: the
episode ends after ~8 s with no pill placed.

Simulated time is what the robot experiences: a planner call's latency elapses in
simulation while the arms hold, so planning delays and outages cost task time.
Wall-clock compute of the stand-in decision code is recorded separately.

## Edge Qwen: the real on-device planner (`device_planner.py`)

The Edge Qwen configuration (`edge_qwen_edge_skills`) has no latency model and no
stand-in. Every skill decision is a real request to the model on a connected
device, and the episode cannot start without that connection.

| Part | What it is | Measured or scripted |
|---|---|---|
| Decision: which arm, which pill, which skill, or wait / done | Qwen2.5-1.5B-Instruct Q4_K_M, llama.cpp CUDA, on a Jetson Orin Nano (the device's active release) | real call per decision |
| Scene the model reads | text computed from the simulator state (privileged) | from the simulator |
| Executive: when to ask, parsing, the choice check, the failure policy, the separation re-check | `device_planner.py`, `episode.py` | scripted, fixed before the run |
| Motion | the IK skill library on the simulator state | scripted |
| Time an arm waits for a decision | each call's measured end-to-end round trip | measured |

**Text only.** Qwen2.5-1.5B-Instruct is a text model: it never sees the camera.
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
- After a transport failure the device is checked. Offline, not eligible for
  chat, a changed model or a refused session stops the episode and the evaluation
  (`planner_stopped:…`; later episodes are reported as not run).
- A call with no terminal result after 45 s is a timeout; its elapsed time counts
  like any round trip, and before the next request the client waits (wall clock
  only) until that request has finished or expired, so the device never has two.
- When an accepted action arrives, the executive re-checks the separation rules on
  the current state (the other arm kept moving during the round trip). If its only
  conflict is that the other arm now uses the bottle zone, the action is held until
  the zone is free (at most 6 s, as a pick next to the bottle waits) and checked
  again; any other conflict, or a hold that runs out, rejects it as stale and a new
  decision starts at once. The model's choice is never changed.

**Transport.** `PortalChatClient` uses the website's device chat routes with one
signed-in operator session: `POST /api/portal/chat` with `{request_id,
expected_release_id, messages, max_tokens}` (202), then `GET
/api/portal/chat/{request_id}` until `succeeded`, `failed` or `expired`; the
control plane relays it (`/api/v1/devices/{device}/chat`) to the agent on the
device, which claims requests once a second and runs them through its gateway.
Limits: user and assistant messages only, at most 16 messages and 8 KiB of text,
1–128 output tokens, one request at a time per device; 6 sends and 180 reads per
minute per session (20 and 1,200 per site); requests expire after 120 s. Sends
are spaced 10.5 s apart; reads poll every 0.25 s for 5 s, then every second. The
API takes no temperature or seed: decoding is the active release's, which the
device reports in its runtime arguments as `--temp 0.0 --seed 42` (greedy),
2,048-token context, 128-token output cap.

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
`OUTPUT/<config>/calls.jsonl`): the messages sent, the raw reply, the platform
trace id (the device's inference trace, listed in its Traces), request id, send
and finish times, HTTP and relay status, on-device latency, first-token and queue
time, tokens in and out, the end-to-end round trip, the parse result and refusal
reason, the action, what followed (delivered, re-asked, failed decision, stale
rejection, stopped) and the simulated start and end times. No credentials.

## Evaluation (`evaluate.py`)

Slices: `nominal` (24 pills, 0.20 × 0.44 m), `pill_count_30`, `scatter_wide`
(0.26 × 0.54 m), `scatter_tight` (0.11 × 0.17 m), `lighting_dim` (frames only: the
skills read state), `pill_near_bottle` (4 pills 4–16 mm from the wall),
`network_outage` (site–cloud link down from t = 15 s to 45 s).

Episodes go to `OUTPUT/<config>/<seed>-<slice>/`. With `--seed-stride N` the i-th
slice runs seeds `i·N + seed`, so every episode of a configuration has its own
seed (Convoy groups an offline evaluation's rollouts by seed), and every
configuration runs the same layouts: a layout depends only on the slice and seed.

### Modelled demo results (stand-in planner)

`scripts/pill_task_eval.sh` (seeds 0–2, stride 100), every configuration on the
same 9 layouts (MuJoCo 3.3.0, 2 ms, 150 s horizon; 35 min on 4 CPU workers). These
are the stand-in configurations: their planner decisions are the rule-based
stand-in and their latency is modeled. The Edge Qwen row this matrix once had (the
stand-in with a latency model) is withdrawn: Edge Qwen now calls the real model,
and its results are [the real run](#real-run-edge-qwen-on-the-jetson).

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
- Edge Qwen (device planner) episodes export measured numbers instead: `planner_ms`
  is the median end-to-end round trip of the calls the model answered, `skill`
  lists the skills it chose and the executive started (e.g. `pick_and_drop ×30,
  push_apart ×3`), and the 32 metrics are the calls by result (valid, invalid JSON,
  schema or choice, device and HTTP errors, timeouts), failed decisions, stale
  rejections, end-to-end and on-device p50/p95, first-token p50, tokens in and out
  p50 and planner wait, besides the task and safety counts. Outage seconds and
  motor-policy availability do not apply and are left out.
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
