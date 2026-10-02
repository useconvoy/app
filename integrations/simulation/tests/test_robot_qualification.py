"""Execute actual imported MuJoCo physics; do not replace the engine with a mock."""
from __future__ import annotations

import hashlib
import io
import zipfile
from copy import deepcopy

import pytest

from convoy_sim.qualification.assets import mujoco_files, read_asset
from convoy_sim.qualification.mujoco import qualify
from convoy_sim.qualification.runner import check

XML = b'''<mujoco model="qualification-arm">
  <compiler angle="radian"/>
  <option timestep="0.002" gravity="0 0 0"/>
  <worldbody><body name="arm"><joint name="shoulder" type="hinge" axis="0 1 0" range="-1 1"/>
    <geom type="capsule" fromto="0 0 0 0 0 0.3" size="0.03" mass="1"/>
  </body></worldbody>
  <actuator><position name="shoulder-position" joint="shoulder" kp="20" kv="2"/></actuator>
</mujoco>'''


def fixture(tmp_path):
    path = tmp_path / "model.xml"
    path.write_bytes(XML)
    model = {"engine": "mujoco", "engine_version": "3.3.0", "controller": "position",
             "asset": {"format": "mjcf", "sha256": hashlib.sha256(XML).hexdigest()}}
    spec = {"command_interface": "joint-position", "control_rate_hz": 50,
            "joints": [{"name": "shoulder", "kind": "revolute", "lower": -1, "upper": 1}], "sensors": []}
    return path, spec, model


def test_real_model_loads_and_moves_under_the_declared_controller(tmp_path):
    path, spec, model = fixture(tmp_path)
    result = qualify(spec, model, path)
    assert result["steps"] == 200
    assert result["sim_seconds"] == pytest.approx(0.4)
    assert result["max_joint_displacement"] > 0
    assert result["joint_names"] == ["shoulder"]
    assert "physics-step" in result["checks"]
    isolated = check(spec, model, path)
    assert isolated["state"] == "passed", isolated


def test_shutdown_interrupts_native_verification_without_reporting_a_pass(tmp_path):
    import multiprocessing

    path, spec, model = fixture(tmp_path)
    previous = {child.pid for child in multiprocessing.active_children()}
    with pytest.raises(InterruptedError):
        check(spec, model, path, stop_requested=lambda: True)
    assert {child.pid for child in multiprocessing.active_children()} == previous


@pytest.mark.parametrize("change", ["digest", "joint", "limits", "controller", "version", "cadence", "camera"])
def test_wrong_profile_or_asset_cannot_pass(tmp_path, change):
    path, spec, model = fixture(tmp_path)
    if change == "digest":
        model["asset"]["sha256"] = "0" * 64
    elif change == "joint":
        spec["joints"][0]["name"] = "elbow"
    elif change == "limits":
        spec["joints"][0]["upper"] = 2
    elif change == "controller":
        spec["command_interface"] = "joint-torque"
        model["controller"] = "torque"
    elif change == "version":
        model["engine_version"] = "0.0.0"
    elif change == "cadence":
        spec["control_rate_hz"] = 123
    else:
        spec["sensors"] = [{"kind": "camera", "name": "missing"}]
    with pytest.raises(ValueError):
        qualify(spec, model, path)


def test_bundle_assets_are_content_addressed_and_cannot_escape(tmp_path):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("model.xml", XML)
    payload = stream.getvalue()
    path, spec, model = fixture(tmp_path)
    path.write_bytes(payload)
    model = deepcopy(model)
    model["asset"].update(format="bundle", sha256=hashlib.sha256(payload).hexdigest())
    assert qualify(spec, model, path)["steps"] == 200
    with pytest.raises(ValueError):
        mujoco_files(b'<mujoco><include file="/etc/passwd"/></mujoco>', "mjcf")
    with pytest.raises(ValueError):
        mujoco_files(b'<mujoco><include file="../outside.xml"/></mujoco>', "mjcf")
    with pytest.raises(ValueError):
        mujoco_files(b'<mujoco><extension><plugin plugin="unknown"/></extension></mujoco>', "mjcf")
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(ValueError):
        read_asset(link, model["asset"]["sha256"])
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("model.xml", XML)
        archive.writestr("../outside.xml", b"bad")
    with pytest.raises(ValueError):
        mujoco_files(stream.getvalue(), "bundle")


def test_unstable_controller_is_a_failed_check(tmp_path):
    path, spec, model = fixture(tmp_path)
    unstable = XML.replace(b'axis="0 1 0"', b'axis="0 0 1"')
    path.write_bytes(unstable)
    model["asset"]["sha256"] = hashlib.sha256(unstable).hexdigest()
    with pytest.raises(ValueError, match="simulator warning"):
        qualify(spec, model, path)


def test_declared_camera_renders_on_the_actual_engine(tmp_path):
    path, spec, model = fixture(tmp_path)
    payload = XML.replace(b"<worldbody>", b'<worldbody><camera name="overview" pos="1 1 1"/>')
    path.write_bytes(payload)
    model["asset"]["sha256"] = hashlib.sha256(payload).hexdigest()
    spec["sensors"] = [{"name": "overview", "kind": "camera"}]
    result = check(spec, model, path)
    assert result["state"] == "passed", result
    assert result["evidence"]["camera_names"] == ["overview"]
    assert "camera-render" in result["evidence"]["checks"]
