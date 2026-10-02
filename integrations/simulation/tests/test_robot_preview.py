"""The website's robot preview scene (scripts/render_robot_preview.py), checked without OpenGL."""

from __future__ import annotations

import importlib.util
import itertools
import math
from pathlib import Path

import mujoco
import numpy as np
import pytest

from convoy_sim.bimanual_pill_task.robot import ARM_JOINTS, SIDES
from convoy_sim.bimanual_pill_task.skills import REST_OPENING

_spec = importlib.util.spec_from_file_location("render_robot_preview", Path(__file__).parents[1] / "scripts" / "render_robot_preview.py")
preview = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(preview)
SIGNS = np.array(list(itertools.product((-1.0, 1.0), repeat=3)))


@pytest.fixture(scope="module")
def studio():
    pose = preview.idle_pose()
    model = mujoco.MjModel.from_xml_string(preview.studio_mjcf(np.full(3, 0.9), 96, 64))
    data = mujoco.MjData(model)
    preview.pose_studio(model, data, pose)
    preview.separate_coplanar_faces(model, model.body("robot").id)
    return pose, model, data


def test_the_idle_pose_is_every_robot_joint_at_the_start_of_an_episode(studio):
    pose, _, _ = studio
    assert sorted(pose) == sorted([f"{side}_{joint}" for side in SIDES for joint in (*ARM_JOINTS, "finger_a", "finger_b")]
                                  + ["head_pan", "head_tilt"])
    assert pose["head_tilt"] == pytest.approx(0.35, abs=0.01)
    for side in SIDES:
        assert pose[f"{side}_finger_a"] + pose[f"{side}_finger_b"] == pytest.approx(REST_OPENING, abs=0.002)
    # The arms mirror each other (the right arm's yaw and roll axes are flipped).
    assert [pose[f"left_{j}"] for j in ARM_JOINTS] == pytest.approx([pose[f"right_{j}"] for j in ARM_JOINTS], abs=1e-3)


def test_the_robot_turns_about_its_base_and_stays_in_frame_all_the_way_round(studio):
    _, model, data = studio
    robot = model.body("robot").id
    geoms = [g for g in range(model.ngeom) if model.body_rootid[model.geom_bodyid[g]] == robot]
    base = next(g for g in geoms if model.geom_bodyid[g] == robot and model.geom_type[g] == mujoco.mjtGeom.mjGEOM_BOX)
    camera = mujoco.MjvCamera()
    camera.type = mujoco.mjtCamera.mjCAMERA_FREE
    camera.lookat[:] = [preview.PIVOT[0], preview.PIVOT[1], preview.LOOK_AT_Z]
    camera.distance, camera.azimuth, camera.elevation = preview.DISTANCE, 180.0, preview.ELEVATION
    scene = mujoco.MjvScene(model, maxgeom=1000)
    half_height = math.tan(math.radians(preview.FOVY / 2))
    first = None
    for k in range(73):
        preview.turn(model, data, preview.START_YAW + 5.0 * k)
        np.testing.assert_allclose(data.geom_xpos[base][:2], preview.PIVOT[:2], atol=1e-9)
        if k == 0:
            first = data.geom_xpos.copy()
        mujoco.mjv_updateScene(model, data, mujoco.MjvOption(), None, camera, mujoco.mjtCatBit.mjCAT_ALL, scene)
        # A mono render looks from between the scene's two stereo eyes.
        eye, forward, up = (np.mean([getattr(scene.camera[i], name) for i in (0, 1)], axis=0) for name in ("pos", "forward", "up"))
        right = np.cross(forward, up)
        for g in geoms:
            corners = model.geom_aabb[g][:3] + SIGNS * model.geom_aabb[g][3:]  # the bounding box, in the geom's frame
            v = data.geom_xpos[g] + corners @ data.geom_xmat[g].reshape(3, 3).T - eye
            depth = v @ forward
            x = (v @ right) / depth / (half_height * 1.5)  # the 3:2 frame
            y = (v @ up) / depth / half_height
            assert np.all(np.abs(x) < 0.95) and np.all(np.abs(y) < 0.95), f"geom {g} leaves the frame at step {k}"
    np.testing.assert_allclose(data.geom_xpos, first, atol=1e-9)  # a full turn ends where it began: a seamless loop


def test_coinciding_faces_are_separated_by_less_than_a_pixel(studio):
    _, model, _ = studio
    original = mujoco.MjModel.from_xml_string(preview.studio_mjcf(np.full(3, 0.9), 96, 64))
    change = np.abs(model.geom_size - original.geom_size)
    assert change.max() > 0, "the rounded boxes' duplicate faces were found"
    assert change.max() < 5e-4  # a 960 px frame spans about 2.5 m here: 2.6 mm a pixel
