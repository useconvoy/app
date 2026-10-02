"""Registration is durable identity, not fabricated physics or execution authority."""
from copy import deepcopy

import pytest
from conftest import WEB, FakeAgent, enrollment_token, login, make_user
from fastapi.testclient import TestClient
from test_platform_lifecycle import post


def spec():
    return {
        "embodiment": "custom-arm", "command_interface": "joint-position", "adapter": "ros2",
        "control_rate_hz": 50,
        "joints": [{"name": "shoulder", "kind": "revolute", "lower": -1, "upper": 1,
                    "evidence": {"source": "imported"}}],
        "dynamics": {"source": "unknown"},
        "simulations": [{"engine": "mujoco", "engine_version": "3.3.0", "controller": "position",
                         "asset": {"uri": "artifact:arm-v1", "sha256": "a" * 64, "format": "mjcf"},
                         "evidence": {"source": "imported"}}],
    }


def profile(admin, project, body=None, revision=0):
    return post(admin, "/api/v1/robot-profiles", {"project_id": project["id"], "name": "Arm",
                "expected_revision": revision, "spec": body or spec()}, key=f"profile-{revision}-{project['id']}")


def register(app, admin, project, prof, simulated=False, **kw):
    agent = FakeAgent(app)
    assert agent.enroll(enrollment_token(admin, simulated=simulated), simulated=simulated).status_code == 200
    body = {"project_id": project["id"], "name": "Sim arm" if simulated else "Physical arm",
            "device_id": agent.device_id, "profile_id": prof["id"],
            "kind": "simulated" if simulated else "physical", **kw}
    if simulated:
        body.setdefault("simulation_engine", "mujoco")
    return post(admin, "/api/v1/robot-registrations", body, key=agent.device_id), body


def test_physical_robot_and_simulation_pin_same_immutable_profile(app, admin):
    project = post(admin, "/api/v1/projects", {"name": "Lab"})
    prof = profile(admin, project)
    physical, body = register(app, admin, project, prof)
    assert physical == post(admin, "/api/v1/robot-registrations", body, key=body["device_id"])
    twin, _ = register(app, admin, project, prof, simulated=True, source_robot_id=physical["id"])
    assert not physical["simulated"] and twin["simulated"]
    assert twin["profile_id"] == physical["profile_id"] == prof["id"]
    assert twin["source_robot_id"] == physical["id"]
    assert prof["simulation"]["state"] == "assets-declared"
    assert prof["simulation"]["runtime_verified"] is False
    changed = spec()
    changed["control_rate_hz"] = 25
    second = profile(admin, project, changed, revision=1)
    assert second["revision"] == 2 and second["digest"] != prof["digest"]
    assert admin.get(f"/api/v1/robots/{physical['id']}").json()["profile_id"] == prof["id"]
    assert admin.get(f"/api/v1/robot-profiles/{prof['id']}").json() == prof
    stale = {"project_id": project["id"], "name": "Arm", "expected_revision": 0, "spec": changed}
    post(admin, "/api/v1/robot-profiles", stale, key="stale", expected=409)
    # Even a claimed known execution profile cannot activate an asset the runner has not qualified.
    post(admin, "/api/v1/deployments", {"robot_id": twin["id"], "release_id": "missing",
         "expected_generation": 0}, expected=409)


def test_fleet_assignment_is_atomic_project_scoped_and_detects_stale_writes(app, admin):
    project = post(admin, "/api/v1/projects", {"name": "Lab"})
    prof = profile(admin, project)
    fleet = post(admin, "/api/v1/fleets", {"project_id": project["id"], "name": "Line 1"})
    first, _ = register(app, admin, project, prof, fleet_id=fleet["id"])
    second, _ = register(app, admin, project, prof)
    path = f"/api/v1/fleets/{fleet['id']}/members"
    assignment = {"assignments": [{"robot_id": second["id"], "expected_fleet_id": None}]}
    result = post(admin, path, assignment, expected=200)
    assert set(result["robot_ids"]) == {first["id"], second["id"]}
    assert post(admin, path, assignment, expected=200) == result
    post(admin, path, assignment, key="stale", expected=409)
    other_project = post(admin, "/api/v1/projects", {"name": "Other"}, key="other")
    other_profile = profile(admin, other_project)
    other_robot, _ = register(app, admin, other_project, other_profile)
    batch = {"assignments": [{"robot_id": first["id"], "expected_fleet_id": fleet["id"]},
                             {"robot_id": other_robot["id"], "expected_fleet_id": None}]}
    post(admin, path, batch, key="wrong-project", expected=404)
    removed = post(admin, f"{path}/{first['id']}/remove", {}, expected=200)
    assert removed["robot_ids"] == [second["id"]]


def test_registry_is_owner_scoped_and_requires_operator(app, admin):
    project = post(admin, "/api/v1/projects", {"name": "Lab"})
    prof = profile(admin, project)
    robot, _ = register(app, admin, project, prof)
    extra = FakeAgent(app)
    extra.enroll(enrollment_token(admin))
    assert [d["id"] for d in admin.get("/api/v1/robot-connections").json()] == [extra.device_id]
    for role in ("viewer", "operator", "admin"):
        email = f"{role}@other.example"
        make_user(admin, email, role)
        with TestClient(app) as other:
            login(other, email, "password-123")
            assert other.get("/api/v1/robot-connections").json() == []
            for path in (f"robot-profiles/{prof['id']}", f"robot-profiles?project_id={project['id']}",
                         f"fleets?project_id={project['id']}", f"robots/{robot['id']}"):
                assert other.get(f"/api/v1/{path}").status_code == 404
            if role == "viewer":
                assert other.post("/api/v1/robot-profiles", json={"project_id": project["id"],
                    "name": "x", "expected_revision": 0, "spec": spec()},
                    headers={**WEB, "Idempotency-Key": "forbidden"}).status_code == 403


def test_source_revision_engine_and_device_kind_must_match(app, admin):
    project = post(admin, "/api/v1/projects", {"name": "Lab"})
    prof = profile(admin, project)
    physical, _ = register(app, admin, project, prof)
    newer = profile(admin, project, revision=1)
    agent = FakeAgent(app)
    agent.enroll(enrollment_token(admin))
    base = {"project_id": project["id"], "name": "Twin", "device_id": agent.device_id,
            "profile_id": newer["id"], "kind": "simulated", "simulation_engine": "mujoco",
            "source_robot_id": physical["id"]}
    post(admin, "/api/v1/robot-registrations", base, key="wrong-revision", expected=409)
    post(admin, "/api/v1/robot-registrations", {**base, "profile_id": prof["id"],
         "simulation_engine": "isaac"}, key="missing-engine", expected=409)
    post(admin, "/api/v1/robot-registrations", {**base, "kind": "physical"}, key="wrong-kind", expected=409)


@pytest.mark.parametrize("change", ["duplicate-joint", "inverted-limits", "missing-joints", "duplicate-engine", "nonfinite"])
def test_invalid_mechanics_are_rejected(admin, change):
    project = post(admin, "/api/v1/projects", {"name": "Lab"})
    body = deepcopy(spec())
    if change == "duplicate-joint":
        body["joints"] *= 2
    elif change == "inverted-limits":
        body["joints"][0]["lower"] = 2
    elif change == "missing-joints":
        body["joints"] = []
    elif change == "duplicate-engine":
        body["simulations"] *= 2
    else:
        body["control_rate_hz"] = "Infinity"
    post(admin, "/api/v1/robot-profiles", {"project_id": project["id"], "name": "Bad",
         "expected_revision": 0, "spec": body}, expected=422)


def test_existing_sqlite_database_gains_registry_without_changing_robots(app, admin, settings):
    from convoy_contracts.execution import PROFILE
    from convoy_server import migrations
    from convoy_server.db import make_engine
    from sqlalchemy import text

    if not settings.db_url.startswith("sqlite"):
        pytest.skip("SQLite additive migration check")
    project = post(admin, "/api/v1/projects", {"name": "Existing"})
    agent = FakeAgent(app)
    agent.enroll(enrollment_token(admin))
    robot = post(admin, "/api/v1/robots", {"project_id": project["id"], "device_id": agent.device_id,
                 "name": "Existing simulator", "profile": PROFILE})
    engine = make_engine(settings)
    try:
        with engine.begin() as connection:
            for table in ("robot_fleet_members", "robot_registrations", "robot_fleets", "robot_profiles"):
                connection.execute(text(f"DROP TABLE {table}"))
        migrations.ensure_schema(engine)
        assert admin.get(f"/api/v1/robots/{robot['id']}").json() == robot
    finally:
        engine.dispose()
