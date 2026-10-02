"""Physics, kinematics, planner timing and replay-format tests for the bimanual pill task.

No OpenGL is needed: recordings use a synthetic frame source. The platform's own
replay reader is exercised when the ``managed`` extra (convoy-server) is installed.
"""

import importlib.util
import json
import math
import zlib
from pathlib import Path
from types import SimpleNamespace

import mujoco
import numpy as np
import pytest

from convoy_sim.bimanual_pill_task import physics as P
from convoy_sim.bimanual_pill_task.configs import CONFIGS, EDGE_QWEN, SLICES, EpisodeSpec, release_manifest
from convoy_sim.bimanual_pill_task.control import ArmKinematics, grasp_rotation
from convoy_sim.bimanual_pill_task.episode import Episode, run_episode
from convoy_sim.bimanual_pill_task.evaluate import task_label
from convoy_sim.bimanual_pill_task.offline_replay import (
    ACTION_LABELS,
    OfflineReplayRecorder,
    write_evaluation,
)
from convoy_sim.bimanual_pill_task.physics_check import drop_check, settle_check
from convoy_sim.bimanual_pill_task.planning import (
    GreedyPillPlanner,
    LatencyModel,
    NetworkModel,
    PlannerCall,
    PlannerEndpoint,
)
from convoy_sim.bimanual_pill_task.policies import ActionChunk, LearnedSkill, make_policy
from convoy_sim.bimanual_pill_task.recording import JournalRecorder, png_rgb8
from convoy_sim.bimanual_pill_task.replay_check import validate_journal
from convoy_sim.bimanual_pill_task.scene import Layout, PillPose, sample_layout
from convoy_sim.bimanual_pill_task.skills import (
    ArmController,
    GraspProbe,
    PickAndDrop,
    PushApart,
    Workspace,
    bezier,
    rest_position,
    transit,
)
from convoy_sim.bimanual_pill_task.world import World


def _world(pills=()):
    return World(Layout(tuple(pills)))


def test_model_matches_the_specified_robot_and_scene():
    world = _world([PillPose(0.5, 0.1, 0.0, 0.0007)])
    m = world.model
    robot = m.body("robot").id
    total = sum(m.body_mass[b] for b in range(m.nbody) if m.body_rootid[b] == robot)
    assert 45 < total < 55  # ~50 kg robot
    for side in ("left", "right"):
        arm = [b for b in range(m.nbody) if m.body(b).name.startswith(f"{side}_")]
        assert 5 < sum(m.body_mass[b] for b in arm) < 7
        hinges = [j for j in range(m.njnt) if m.jnt_type[j] == mujoco.mjtJoint.mjJNT_HINGE
                  and m.joint(j).name.startswith(side)]
        assert len(hinges) == 6  # 6-DOF arm
        stroke = sum(m.jnt_range[m.joint(f"{side}_finger_{k}").id][1] for k in ("a", "b"))
        assert stroke == pytest.approx(0.080)  # 0-80 mm parallel gripper
        assert m.camera(f"{side}_wrist_camera") is not None
    assert m.camera("head_camera") is not None
    assert m.opt.timestep == P.TIMESTEP_S and 0.001 <= m.opt.timestep <= 0.002
    pill = m.geom("pill_00")
    assert pill.size[0] * 2 == pytest.approx(0.008) and (pill.size[1] + pill.size[0]) * 2 == pytest.approx(0.020)
    assert 0.0005 <= m.body_mass[m.body("pill_00").id] <= 0.001
    mujoco.mj_forward(m, world.data)
    assert world.data.geom("mat").xpos[2] + m.geom("mat").size[2] == pytest.approx(P.MAT_TOP_M)


def test_closed_form_ik_reaches_the_pill_region_exactly():
    world = _world()
    rng = np.random.default_rng(0)
    for side, sign in (("left", 1.0), ("right", -1.0)):
        kin = ArmKinematics(world.model, side)
        hits = 0
        for _ in range(60):
            pos = np.array([rng.uniform(0.32, 0.58), sign * rng.uniform(-0.02, 0.24), 0.76])
            rot = grasp_rotation(rng.uniform(-math.pi, math.pi), float(rng.choice([0.0, 0.3])))
            # A parallel gripper is symmetric: the same approach with the fingers swapped.
            for candidate in (rot, rot @ np.diag([1.0, -1.0, -1.0])):
                q = kin.analytic(pos, candidate)
                if q is not None:
                    break
            if q is None:
                continue
            hits += 1
            p, r = kin.fk(q)
            assert np.linalg.norm(p - pos) < 1e-9 and np.abs(r - candidate).max() < 1e-9
            assert np.all(q >= kin.lower - 1e-9) and np.all(q <= kin.upper + 1e-9)
        assert hits >= 57  # the whole pill region is reachable at almost any yaw


def test_pills_rest_without_jitter_and_do_not_tunnel():
    settle = settle_check(seed=3)
    assert settle["max_speed_m_s"] < 1e-3
    assert settle["max_drift_over_2s_m"] < 2e-4
    assert settle["max_penetration_m"] < 5e-5
    # The task's fastest pill: released 2.8 cm above the mouth, falling to the
    # bottle floor, at most ~15 cm (1.7 m/s). The centre never dips below the mat.
    drop = drop_check(0.15)
    assert not drop["tunnelled"] and not drop["centre_dipped_below_surface"]
    assert drop["max_penetration_m"] < P.PILL_RADIUS_M
    assert drop["max_speed_after_1_5s_m_s"] < 1e-3
    # Faster impacts (0.3 m, 2.4 m/s) dip deeper at 2 ms but never pass through.
    assert not drop_check(0.30)["tunnelled"]


def test_one_pick_and_drop_places_the_pill_in_the_bottle():
    world = _world([PillPose(0.50, 0.12, 0.7, 0.0008)])
    arms = {s: ArmController(world, s) for s in ("left", "right")}
    for side, arm in arms.items():
        arm.reset_to(rest_position(side), math.pi / 2, 0.03)
    world.set_head(world.robot.head_pan, world.robot.head_tilt)
    mujoco.mj_forward(world.model, world.data)
    space = Workspace(world, arms)
    skill = PickAndDrop(world, arms["left"], GraspProbe(world, arms["left"]), 0, space, 0.0)
    per_tick = int(round(0.01 / P.TIMESTEP_S))
    t = 0.0
    while not skill.done and t < 15:
        skill.update(t, 0.01)
        arms["right"].hold(0.01)
        world.step(per_tick)
        t += 0.01
    assert skill.result.status == "placed", skill.result
    assert world.pill(0).in_bottle
    assert world.max_robot_contact_force < 50  # no hard contact with the table or bottle


def test_push_apart_singulates_a_side_by_side_pair():
    # Two capsules side by side with a ~4 mm gap (left over in pill_count_30 seed 2
    # before pushes could brush a neighbour): no finger fits between them.
    world = _world([PillPose(0.4333, 0.1351, -0.828, 0.0008), PillPose(0.4280, 0.1235, 2.724, 0.0008)])
    arms = {s: ArmController(world, s) for s in ("left", "right")}
    for side, arm in arms.items():
        arm.reset_to(rest_position(side), math.pi / 2, 0.03)
    mujoco.mj_forward(world.model, world.data)
    probe = GraspProbe(world, arms["left"])
    assert probe.choose(0).clearance < 0
    skill = PushApart(world, arms["left"], probe, 0, Workspace(world, arms), 0.0)
    per_tick = int(round(0.01 / P.TIMESTEP_S))
    t = 0.0
    while not skill.done and t < 8:
        skill.update(t, 0.01)
        arms["right"].hold(0.01)
        world.step(per_tick)
        t += 0.01
    assert skill.result.status == "pushed", skill.result
    assert probe.choose(0).clearance >= 0  # now graspable
    assert world.pill(0).on_mat and world.pill(1).on_mat
    assert world.max_robot_contact_force < 50


def test_transits_are_lifted_until_the_gripper_clears_the_bottle():
    world = _world()
    arm = ArmController(world, "right")
    arm.reset_to(rest_position("right"), math.pi / 2, 0.03)
    mujoco.mj_forward(world.model, world.data)
    probe = GraspProbe(world, arm)
    # Low poses on either side of the bottle, wrist leaning 0.3 rad.
    start, end, yaw, tilt = np.array([0.445, 0.024, 0.779]), np.array([0.302, -0.012, 0.816]), -1.24, 0.3
    arm.q, arm.pos, arm.yaw, arm.tilt = probe.ik(start, yaw, tilt), start.copy(), yaw, tilt
    plain = bezier(start, end, 0.0, yaw, yaw, 0.02, 0.02, tilt0=tilt, tilt1=tilt)
    lifted = transit(probe, start, end, 0.0, yaw, yaw, 0.02, 0.02, tilt0=tilt, tilt1=tilt)
    assert not probe.path_clear(plain)  # the default transit height clips the bottle
    assert probe.path_clear(lifted) and lifted.points[1][2] > plain.points[1][2]


class _NoDeviceCalls:
    """A device connection that must not be used (the test drives the executive directly)."""

    release_id = "rel_unused"

    def device(self):
        raise AssertionError("no device call expected")

    def send(self, messages, max_tokens):
        raise AssertionError("no device call expected")


@pytest.mark.parametrize("config", ["edge_qwen_edge_skills", "cloud_astra_only"])
def test_executive_rejects_a_stale_decision_next_to_the_other_arm(config):
    planner = _NoDeviceCalls() if CONFIGS[config].skill_planner.source == "device" else None
    episode = Episode(EpisodeSpec(CONFIGS[config], SLICES["nominal"], 0, 30.0), planner=planner)
    pills = episode.world.pills()
    target = next(p for p in pills if p.pos[1] < -0.05)
    near = next(p for p in pills if p.index != target.index and np.linalg.norm(p.pos[:2] - target.pos[:2]) < 0.10)
    far = next(p for p in pills if p.pos[1] > 0.05 and np.linalg.norm(p.pos[:2] - target.pos[:2]) > 0.2)
    # The right arm has started on `target` since the left arm's request was sent.
    episode.arms["right"].skill = SimpleNamespace(pill=target.index, done=False, phase="approach")

    def deliver(pill):
        decision = {"kind": "skill", "skill_id": "pick_and_drop", "parameters": {"pill": f"pill_{pill:02d}", "arm": "left"}}
        episode._on_skill_call(PlannerCall(1, "left", "skill", 0.0, {}, decision, "ok"), 0.0)

    deliver(near.index)
    assert episode.rejected_decisions == 1 and episode.arms["left"].skill is None
    deliver(far.index)
    assert episode.rejected_decisions == 1 and episode.arms["left"].skill.pill == far.index


def test_cloud_calls_fail_during_an_outage_and_edge_calls_do_not():
    network = NetworkModel(rtt=LatencyModel(0.03, 0.09), outages=((10.0, 20.0),))
    cloud = CONFIGS["cloud_astra_only"].skill_planner
    edge = EDGE_QWEN  # the modeled edge profile (the skill router of "Edge Qwen + GPT Astra")

    class Fixed:
        def decide(self, request):
            return {"kind": "wait"}, None

    def call(profile, t):
        endpoint = PlannerEndpoint(profile, network, np.random.default_rng(0), Fixed())
        endpoint.submit("left", {}, t)
        done = []
        clock = t
        while not done:
            done = endpoint.poll(clock)
            clock += 0.01
        return done[0]

    before = call(cloud, 0.0)
    assert before.status == "ok" and 1.5 <= before.latency_s <= 12.0
    during = call(cloud, 12.0)
    assert during.status == "unreachable" and during.latency_s == pytest.approx(cloud.connect_timeout_s)
    overlapping = call(cloud, 8.0)  # response window crosses the outage start
    assert overlapping.status == "timeout" and overlapping.latency_s == pytest.approx(cloud.timeout_s)
    assert call(edge, 12.0).status == "ok"
    samples = [LatencyModel(0.150, 0.700).sample(np.random.default_rng(i)) for i in range(4000)]
    assert np.percentile(samples, 50) == pytest.approx(0.150, rel=0.08)
    assert np.percentile(samples, 95) == pytest.approx(0.700, rel=0.12)


def test_stand_in_planner_keeps_arms_on_their_own_side():
    obs = {"pills": [{"id": "pill_00", "xy": [0.5, -0.15], "yaw": 0.0, "state": "on_mat", "attempts": 0},
                     {"id": "pill_01", "xy": [0.5, 0.15], "yaw": 0.0, "state": "on_mat", "attempts": 0}],
           "arms": {side: {"tcp": [0.33, s * 0.3, 0.87], "shoulder": [0.08, s * 0.17, 1.13], "busy": False,
                           "links_xy": [[0.2, s * 0.2], [0.3, s * 0.3], [0.33, s * 0.3]], "target": None,
                           "target_xy": None} for side, s in (("left", 1), ("right", -1))},
           "bottle": {"xy": [0.37, 0.0]}, "zone_owner": None,
           "motor_policy": {"available": True}, "history": []}
    planner = GreedyPillPlanner()
    assert planner.decide({"arm": "left", "observation": obs})[0]["parameters"]["pill"] == "pill_01"
    assert planner.decide({"arm": "right", "observation": obs})[0]["parameters"]["pill"] == "pill_00"
    obs["motor_policy"]["available"] = False
    obs["history"] = [{"pill": "pill_00", "arm": "right", "status": "policy_unavailable"}]
    assert planner.decide({"arm": "left", "observation": obs})[0]["kind"] == "decline"


def test_png_encoder_writes_valid_rgb8():
    image = (np.indices((48, 64, 3)).sum(axis=0) * 7 % 256).astype(np.uint8)
    data = png_rgb8(image)
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR"
    width, height = int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    assert (width, height, data[24], data[25]) == (64, 48, 8, 2)
    raw = zlib.decompress(data[41:-12])
    rows = np.frombuffer(raw, dtype=np.uint8).reshape(48, 1 + 64 * 3)
    previous = np.zeros(64 * 3, dtype=np.int16)
    for y, row in enumerate(rows):  # undo the five PNG filters
        kind, line = row[0], row[1:].astype(np.int16)
        out = np.zeros(64 * 3, dtype=np.int16)
        for x in range(64 * 3):
            a = out[x - 3] if x >= 3 else 0
            b = previous[x]
            c = previous[x - 3] if x >= 3 else 0
            p = a + b - c
            pred = [0, a, b, (a + b) // 2,
                    a if abs(p - a) <= abs(p - b) and abs(p - a) <= abs(p - c) else b if abs(p - b) <= abs(p - c) else c][kind]
            out[x] = (line[x] + pred) % 256
        assert np.array_equal(out.astype(np.uint8), image[y].reshape(-1))
        previous = out


@pytest.fixture(scope="module")
def recorded(tmp_path_factory):
    out = tmp_path_factory.mktemp("rec") / "episode"
    config = CONFIGS["edge_smolvla_cloud_astra"]  # short: the planner declines after the policy reports unavailable
    rng = np.random.default_rng(0)

    def frames(world):
        return rng.integers(0, 255, size=(48, 48, 3), dtype=np.uint8)

    recorder = JournalRecorder(out, release_manifest=release_manifest(config), frame_source=frames)
    summary = run_episode(EpisodeSpec(config, SLICES["nominal"], 0, horizon_s=30.0), recorder)
    return out, summary


def test_unavailable_learned_policy_is_reported_not_faked(recorded):
    _, summary = recorded
    assert summary["outcome"] == "planner_declined:no_motor_policy_for_this_robot"
    assert summary["placed"] == 0
    assert set(summary["skills"]) == {"policy_unavailable"}
    assert summary["planner_latency_p50_ms"] > 1500  # cloud planner latency was modeled


def test_recording_follows_the_platform_replay_format(recorded):
    out, summary = recorded
    checked = validate_journal(out)
    assert checked["steps"] == summary["recording"]["steps"] > 10
    episode = json.loads((out / "episode.json").read_text())
    assert episode["summary"]["planner_result"]["planner_duration_ms"] > 0
    assert episode["state"] == "failed"
    replay = pytest.importorskip("convoy_server.services.replay")
    from convoy_sim.bimanual_pill_task.replay_check import platform_reader

    result = platform_reader(out)
    assert result["manifest"]["steps"] == checked["steps"]
    assert result["manifest"]["planner_ms"] == episode["summary"]["planner_result"]["planner_duration_ms"]
    assert all(len(f["action"]) == 4 for f in result["frames"] if f["action"] is not None)
    assert replay.MAX_STEPS >= checked["steps"]


def test_existing_recordings_are_not_overwritten(recorded):
    out, _ = recorded
    recorder = JournalRecorder(out, release_manifest={}, frame_source=lambda world: np.zeros((8, 8, 3), np.uint8))
    with pytest.raises(FileExistsError):
        run_episode(EpisodeSpec(CONFIGS["edge_smolvla_cloud_astra"], SLICES["nominal"], 0, horizon_s=5.0), recorder)


def test_same_seed_same_layout_and_outcome():
    a = sample_layout(np.random.default_rng(5), 24, (0.49, 0.0), (0.1, 0.22))
    b = sample_layout(np.random.default_rng(5), 24, (0.49, 0.0), (0.1, 0.22))
    assert a == b
    spec = EpisodeSpec(CONFIGS["cloud_astra_only"], SLICES["nominal"], 2, horizon_s=6.0)
    first, second = run_episode(spec), run_episode(spec)
    for key in ("placed", "skills", "planner_calls", "simulated_duration_s", "planner_latency_p50_ms"):
        assert first[key] == second[key]


class _LiftPolicy:
    """Test policy: each chunk raises the TCP 5 cm over ten 0.1 s actions; done after two chunks."""

    name = "test-lift"

    def __init__(self, arm):
        self.arm, self.calls = arm, 0

    def availability(self, embodiment):
        return True, ""

    def start(self, skill_id, parameters, arm):
        self.calls = 0

    def infer(self, observation):
        self.calls += 1
        pos, _ = self.arm.kin.fk(np.array(observation["q"]))
        targets = [self.arm.solve(pos + [0, 0, 0.005 * (k + 1)], self.arm.yaw, self.arm.tilt) for k in range(10)]
        return ActionChunk(np.array(targets), np.full(10, 0.03), 0.1, done=self.calls >= 2), None


@pytest.mark.parametrize("latency_s", [0.3, 1.5])
def test_learned_policy_hook_applies_latency_and_never_executes_stale_actions(latency_s):
    world = _world([PillPose(0.5, 0.12, 0.0, 0.0008)])
    arm = ArmController(world, "left")
    arm.reset_to(rest_position("left"), math.pi / 2, 0.03)
    other = ArmController(world, "right")
    other.reset_to(rest_position("right"), math.pi / 2, 0.03)
    mujoco.mj_forward(world.model, world.data)
    start_z = arm.pos[2]
    skill = LearnedSkill(world, arm, _LiftPolicy(arm), "lift", 0, 0.0, LatencyModel(latency_s, latency_s),
                         np.random.default_rng(0), timeout_s=5.0)
    first_motion = None
    t = 0.0
    while not skill.done:
        skill.update(t, 0.01)
        other.hold(0.01)
        world.step(int(round(0.01 / P.TIMESTEP_S)))
        if first_motion is None and arm.pos[2] > start_z + 1e-4:
            first_motion = t
        t += 0.01
    if latency_s < 1.0:
        assert first_motion == pytest.approx(latency_s, abs=0.02)  # nothing moves before inference returns
        assert skill.executed >= 10 and arm.tcp[2] - start_z > 0.04
        assert skill.result.status == "missed"  # the lift policy never places the pill
    else:
        # Every chunk arrives after its 1 s validity horizon: no action is executed.
        assert first_motion is None and skill.executed == 0 and skill.stale_dropped >= 10
        assert skill.result.status == "timeout"


def test_unavailable_policy_reports_its_reason():
    world = _world([PillPose(0.5, 0.12, 0.0, 0.0008)])
    arm = ArmController(world, "left")
    smolvla = CONFIGS["edge_smolvla_cloud_astra"].motor
    skill = LearnedSkill(world, arm, make_policy(smolvla.name, smolvla.reason), "pick_and_drop", 0, 0.0,
                         smolvla.latency, np.random.default_rng(0))
    assert skill.done and skill.result.status == "policy_unavailable"
    assert "no checkpoint for this embodiment" in skill.result.detail


def _importer():
    """scripts/import_offline_eval.py: the reader the offline import uses (standard library only)."""
    path = Path(__file__).resolve().parents[1] / "scripts" / "import_offline_eval.py"
    spec = importlib.util.spec_from_file_location("import_offline_eval", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _SteppedFrames:
    """Synthetic camera: the picture changes on every third call, so repeated frames occur."""

    def __init__(self):
        self.calls = 0

    def __call__(self, world):
        shade = (self.calls // 3) * 40 % 256
        self.calls += 1
        image = np.zeros((32, 32, 3), np.uint8)
        image[8:24, 8:24] = shade
        return image


@pytest.fixture(scope="module")
def offline(tmp_path_factory):
    out = tmp_path_factory.mktemp("offline") / "0000-nominal"
    config = CONFIGS["edge_smolvla_cloud_astra"]  # short: the planner declines once SmolVLA reports unavailable
    recorder = OfflineReplayRecorder(out, image_format="png", frame_source=_SteppedFrames(), max_steps=12)
    summary = run_episode(EpisodeSpec(config, SLICES["nominal"], 0, horizon_s=30.0), recorder)
    write_evaluation(out.parent, config, task_label(["nominal"], {"nominal": [0]}))
    return out, summary


def test_offline_replay_is_what_the_import_reads(offline):
    out, summary = offline
    importer = _importer()
    body = importer.read_episode(out)
    steps, images, _ = importer.describe(body)
    importer.encode(body)  # within the upload limit
    replay = json.loads((out / "replay.json").read_text())
    assert steps == replay["steps"] == summary["replay"]["steps"] <= 12  # capped: <= 13 frames
    assert body["outcome"] == "failure" and body["seed"] == 0 and body["action_labels"] == list(ACTION_LABELS)
    assert body["sim_seconds"] == summary["simulated_duration_s"] and "wall_seconds" not in body
    first, rest = body["frames"][0], body["frames"][1:]
    assert first["image_png_base64"] and (first["action"], first["reward"], first["policy_ms"]) == (None, None, None)
    assert all(len(f["action"]) == 8 and all(-1 <= v <= 1 for v in f["action"]) for f in rest)
    assert 1 < images < len(body["frames"])  # unchanged frames repeat the previous image
    metrics = body["metrics"]
    assert len(metrics) <= 32 and len(json.dumps(metrics)) <= 4096
    assert metrics["pills_placed"] == 0 and metrics["motor_policy_available"] is False
    assert metrics["skills_refused"] >= 1 and metrics["end_reason"].startswith("planner_declined")
    labels = json.loads((out.parent / "evaluation.json").read_text())
    assert labels["name"] == "Pills to bottle · Edge SmolVLA + GPT Astra"
    assert "SmolVLA-450M" in labels["config_label"] and "unavailable" in labels["policy_label"]
    service = pytest.importorskip("convoy_server.services.offline_evaluations")
    episode = service.decode_episode(importer.encode(body))  # the server's own validation
    assert episode.action_dim == 8 and len(episode.images) == images


def test_last_step_runs_to_the_end_when_steps_are_capped(offline):
    out, summary = offline
    replay = json.loads((out / "replay.json").read_text())
    last = json.loads((out / "frames" / f"{replay['steps']:04d}.json").read_text())
    assert replay["steps"] == 12 and summary["simulated_duration_s"] > 12 * 0.5
    assert last["reward"] == 0.0 and last["success"] is False


def test_offline_replay_refuses_to_overwrite(offline):
    out, _ = offline
    recorder = OfflineReplayRecorder(out, image_format="png", frame_source=lambda world: np.zeros((8, 8, 3), np.uint8))
    with pytest.raises(FileExistsError):
        run_episode(EpisodeSpec(CONFIGS["edge_smolvla_cloud_astra"], SLICES["nominal"], 0, horizon_s=5.0), recorder)


def test_metrics_and_outcomes_follow_the_import_rules():
    import re

    from convoy_sim.bimanual_pill_task.offline_replay import episode_metrics, outcome

    summary = {"config": "edge_qwen_edge_skills", "slice": "nominal", "outcome": "all_pills_in_bottle", "success": True,
               "pills": 24, "placed": 24, "fraction_placed": 1.0, "time_to_all_placed_s": 88.1, "planner_calls": 40,
               "planner_failures": 0, "planner_latency_p50_ms": 150.2, "planner_latency_p95_ms": 690.0,
               "planner_wait_s": 9.1, "skill_attempts": 26, "skills": {"placed": 23, "pushed": 2, "no_clear_grasp": 1},
               "wall_duration_s": 240.0}
    metrics = episode_metrics(summary)
    assert all(re.fullmatch(r"[a-z][a-z0-9_]{0,47}", name) for name in metrics)
    assert all(value is None or isinstance(value, bool | int | float) or (isinstance(value, str) and len(value) <= 200)
               for value in metrics.values())
    assert (metrics["pushes"], metrics["skills_refused"], metrics["picks_dropped"]) == (2, 1, 0)
    assert outcome(summary) == "success"
    assert outcome({**summary, "success": False, "outcome": "horizon"}) == "timeout"
    assert outcome({**summary, "success": False, "outcome": "planner_declined:no_motor_policy_for_this_robot"}) == "failure"
    assert outcome({"status": "error"}) == "failure"


def test_jpeg_frames_fit_the_import_limits():
    pytest.importorskip("PIL")
    from convoy_sim.bimanual_pill_task.offline_replay import encode_jpeg

    image = (np.indices((256, 256, 3)).sum(axis=0) * 5 % 256).astype(np.uint8)
    data = encode_jpeg(image)
    assert data[:2] == b"\xff\xd8" and data[-2:] == b"\xff\xd9" and len(data) <= 256 * 1024
    recordings = pytest.importorskip("convoy_server.services.offline_recordings")
    assert recordings.image(data) == ("image/jpeg", 256, 256)


def test_labels_fit_the_platform():
    for config in CONFIGS.values():
        assert 0 < len(config.deployment) <= 120 and 0 < len(config.policy) <= 120
    seeds = {"nominal": [0, 1, 2], "network_outage": [100, 101, 102], "pill_count_30": [200, 201, 202]}
    label = task_label(list(seeds), seeds)
    assert label == "Pills to bottle · nominal (seeds 0–2), cloud outage 15–45 s (seeds 100–102), 30 pills (seeds 200–202)"
    assert len(label) <= 120


def test_a_hosted_planner_answers_both_arms_at_once_and_the_edge_queues():
    class Fixed:
        def decide(self, request):
            return {"kind": "wait"}, None

    def finish_times(profile):
        endpoint = PlannerEndpoint(profile, NetworkModel(), np.random.default_rng(0), Fixed())
        endpoint.submit("left", {}, 0.0)
        endpoint.submit("right", {}, 0.0)
        done, clock = {}, 0.0
        while len(done) < 2:
            for call in endpoint.poll(clock):
                done[call.arm] = (call.started_s, call.completes_s)
            clock = round(clock + 0.01, 2)
        return done

    cloud = finish_times(CONFIGS["cloud_astra_only"].skill_planner)
    assert cloud["left"][0] == cloud["right"][0] == 0.0  # both calls start at once
    edge = finish_times(EDGE_QWEN)
    assert edge["left"][0] == 0.0 and edge["right"][0] >= edge["left"][1]  # one call at a time on the Jetson
