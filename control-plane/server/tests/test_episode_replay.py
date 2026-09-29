"""Replay preserves project authorization and refuses mismatched journal evidence."""
import base64
import json
import sqlite3
from types import SimpleNamespace

import pytest
from conftest import login, make_user
from convoy_server.services import replay
from fastapi import HTTPException
from fastapi.testclient import TestClient
from test_platform_lifecycle import claim, pipeline, start  # noqa: F401


def journal(tmp_path, monkeypatch, episode):
    path = tmp_path / "execution.sqlite3"
    png = base64.b64encode(b"\x89PNG\r\n\x1a\nfixture").decode()
    observation = {"image_png_base64": png, "state": [0, 0, 0, 0]}
    request = {"identity": episode.identity, "sequence": 0, "request_id": "req_1", "observation_id": "obs_1", "observation": observation}
    result = {**{k: v for k, v in request.items() if k != "observation"}, "action": [0.1, -0.2, 0.3, -1], "policy_duration_ms": 4}
    outcome = {"observation": observation, "reward": 1.0, "success": True}
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE missions(id,identity_json,state,report_json); CREATE TABLE commands(mission_id,sequence,request_json,result_json,observation_json,state);")
        db.execute("INSERT INTO missions VALUES (?,?,?,?)", (episode.mission_id, json.dumps(episode.identity), episode.state, json.dumps({"state": episode.state, "summary": episode.summary})))
        db.execute("INSERT INTO commands VALUES (?,?,?,?,?,?)", (episode.mission_id, 0, json.dumps(request), json.dumps(result), json.dumps(outcome), "applied"))
    monkeypatch.setenv("CONVOY_REPLAY_JOURNALS", json.dumps({episode.identity["robot_id"]: str(path)}))
    return path


def test_recording_frames_are_bound_to_terminal_episode(tmp_path, monkeypatch):
    identity = {"mission_id": "mis_one", "robot_id": "rob_one", "release_digest": "a" * 64}
    episode = SimpleNamespace(id="epi_one", mission_id="mis_one", identity=identity, release_digest="a" * 64, state="completed", summary={"steps": 1})
    path = journal(tmp_path, monkeypatch, episode)
    assert replay.manifest(episode)["steps"] == 1
    assert replay.frame(episode, 0)["action"] is None
    assert replay.frame(episode, 1)["action"] == [0.1, -0.2, 0.3, -1]
    for index in (-1, 2):
        with pytest.raises(HTTPException):
            replay.frame(episode, index)
    with sqlite3.connect(path) as db:
        result = json.loads(db.execute("SELECT result_json FROM commands").fetchone()[0])
        result["identity"]["release_digest"] = "b" * 64
        db.execute("UPDATE commands SET result_json=?", (json.dumps(result),))
    with pytest.raises(HTTPException):
        replay.manifest(episode)
    with pytest.raises(HTTPException):
        replay.frame(episode, 1)


def test_recording_routes_require_episode_ownership(pipeline, app, tmp_path, monkeypatch):  # noqa: F811
    p = pipeline
    mission = start(p)
    claimed, _ = claim(p, mission)
    route = f"{p['base']}/missions/{mission['id']}/report"
    assert p["agent"].client.post(route, json={"identity": claimed["identity"], "state": "running"}).status_code == 200
    response = p["agent"].client.post(route, json={"identity": claimed["identity"], "state": "completed", "summary": {"steps": 1}})
    assert response.status_code == 200, response.text
    episode = SimpleNamespace(**response.json()["episode"])
    journal(tmp_path, monkeypatch, episode)
    base = f"/api/v1/episodes/{episode.id}/replay"
    assert p["admin"].get(base).status_code == 200
    assert p["admin"].get(base + "/frames/1").status_code == 200
    make_user(p["admin"], "unrelated@example.com", "operator")
    other = login(TestClient(app), "unrelated@example.com", "password-123")
    anonymous = TestClient(app)
    for path in (base, base + "/frames/1"):
        assert other.get(path).status_code == 404
        assert anonymous.get(path).status_code == 401
    monkeypatch.delenv("CONVOY_REPLAY_JOURNALS")
    assert p["admin"].get(base).status_code == 404
    assert p["admin"].get(f"/api/v1/episodes/{episode.id}").status_code == 200
