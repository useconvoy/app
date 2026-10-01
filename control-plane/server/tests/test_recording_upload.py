"""Authenticated remote evidence survives retries without publishing partial data."""
import json
import sqlite3
from types import SimpleNamespace

import pytest
from conftest import FakeAgent, enrollment_token
from convoy_server.services import replay
from fastapi.testclient import TestClient
from test_episode_replay import journal
from test_platform_lifecycle import claim, pipeline, start  # noqa: F401


def completed(p, tmp_path, monkeypatch):
    mission = start(p)
    claimed, _ = claim(p, mission)
    route = f"{p['base']}/missions/{mission['id']}/report"
    p['agent'].client.post(route, json={'identity': claimed['identity'], 'state': 'running'})
    response = p['agent'].client.post(route, json={'identity': claimed['identity'], 'state': 'completed', 'summary': {'steps': 1}})
    assert response.status_code == 200
    episode = SimpleNamespace(**response.json()['episode'])
    path = journal(tmp_path, monkeypatch, episode)
    monkeypatch.delenv('CONVOY_REPLAY_JOURNALS')
    with sqlite3.connect(path) as db:
        row = db.execute('SELECT request_json,result_json,observation_json FROM commands').fetchone()
    return episode, dict(zip(('request','result','outcome'), map(json.loads, row), strict=True))


def test_upload_publish_retry_and_authorization(pipeline, app, tmp_path, monkeypatch):  # noqa: F811
    p = pipeline
    episode, payload = completed(p, tmp_path, monkeypatch)
    upload = f"{p['base']}/missions/{episode.mission_id}/recording"
    read = f'/api/v1/episodes/{episode.id}/replay'
    assert TestClient(app).post(upload + '/commands/0', json=payload).status_code == 401
    assert p['admin'].post(upload + '/commands/0', json=payload).status_code == 401
    other_device = FakeAgent(app)
    assert other_device.enroll(enrollment_token(p['admin'])).status_code == 200
    assert other_device.client.post(upload + '/commands/0', json=payload).status_code == 404
    agent = p['agent'].client
    assert agent.post(upload + '/publish').status_code == 409
    assert agent.post(upload + '/commands/0', json=payload).status_code == 200
    assert p['admin'].get(read).status_code == 404  # Not published yet.
    assert agent.post(upload + '/commands/0', json=payload).status_code == 200
    assert agent.post(upload + '/publish').status_code == 200
    assert p['admin'].get(read).json()['steps'] == 1
    assert p['admin'].get(read + '/frames/1').json()['action'] == payload['result']['action']
    assert agent.post(upload + '/publish').status_code == 200
    payload['result']['action'][0] = 0.9
    assert agent.post(upload + '/commands/0', json=payload).status_code == 409
    assert agent.post(upload.replace(episode.mission_id, 'mis_unrelated') + '/commands/0', json=payload).status_code == 404


@pytest.mark.parametrize('fault', ['identity', 'image', 'quota', 'bounds', 'oversized'])
def test_bad_upload_stays_unpublished(pipeline, tmp_path, monkeypatch, fault):  # noqa: F811
    episode, payload = completed(pipeline, tmp_path, monkeypatch)
    route = f"{pipeline['base']}/missions/{episode.mission_id}/recording/commands/0"
    expected = 422
    if fault == 'identity':
        payload['result']['identity']['release_digest'] = 'f' * 64
    elif fault == 'image':
        payload['request']['observation']['image_png_base64'] = 'not a png'
    elif fault == 'quota':
        monkeypatch.setenv('CONVOY_RECORDING_QUOTA_BYTES','1024')
        expected = 507
    elif fault == 'bounds':
        route = route[:-1] + '2049'
        expected = 409
    elif fault == 'oversized':
        payload['extra'] = 'x' * (2 * replay.MAX_ROW_BYTES)
        expected = 413
    assert pipeline['agent'].client.post(route, json=payload).status_code == expected
    assert pipeline['admin'].get(f'/api/v1/episodes/{episode.id}/replay').status_code == 404
