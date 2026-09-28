"""Real API → durable evaluation jobs → worker → MuJoCo release qualification.

Run with the simulation managed extra. Uses a fresh private local SQLite stack,
scripted reference policy, two fixed seeds, and a real evaluation-worker restart.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import httpx
from pipeline import wait_for


def run(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    env = {**os.environ, 'CONVOY_DATA_DIR': str(output / 'stack' / 'server'), 'CONVOY_SQLITE_WAL': '0',
           'CONVOY_SIMULATOR': '1', 'CONVOY_SCHEDULER_INPROCESS': '0'}
    env.pop('DATABASE_URL', None)
    processes, logs = [], []

    def start(label, *command):
        stream = (output / (label + '.log')).open('w')
        logs.append(stream)
        process = subprocess.Popen([sys.executable, *command], env=env, stdout=stream, stderr=subprocess.STDOUT)
        processes.append(process)
        return process

    def stop(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    evidence = {'scope': 'scripted policy, real MuJoCo physics, offline lockstep', 'seeds': [0, 1]}
    try:
        harness = start('stack', str(Path(__file__).with_name('pipeline.py')), '--serve', '--output', str(output / 'stack'))
        connection_path = output / 'stack' / 'connection.json'

        def connection():
            if harness.poll() is not None:
                raise RuntimeError('local pipeline stopped; inspect stack.log')
            try:
                return json.loads(connection_path.read_text())
            except (FileNotFoundError, json.JSONDecodeError):
                return None

        settings = wait_for(connection, bool, timeout=40)
        with httpx.Client(base_url=settings['api_url'], timeout=5) as api:
            api.post('/api/v1/auth/login', json={'email': settings['email'], 'password': settings['password']}).raise_for_status()
            api.headers['X-Convoy-Client'] = 'web'

            def post(path, body, expected=201, key=None):
                response = api.post(path, json=body, headers={'Idempotency-Key': key or str(uuid.uuid4())})
                assert response.status_code == expected, response.text
                return response.json()

            def get(path):
                response = api.get(path)
                response.raise_for_status()
                return response.json()

            application = get('/api/v1/applications?project_id=' + settings['project_id'])[0]
            suite = post(f"/api/v1/applications/{application['id']}/evaluation-suites", {
                'name': 'Two-seed manipulation regression', 'reference_release_id': settings['release_id'],
                'seeds': [0, 1], 'min_successes': 2,
            })
            post(f"/api/v1/applications/{application['id']}/evaluation-gate", {
                'suite_id': suite['id'], 'expected_generation': 0,
            }, expected=200)
            post('/api/v1/deployments', {'robot_id': settings['robot_id'], 'release_id': settings['release_id'],
                                       'expected_generation': 1}, expected=409)
            request = {'suite_id': suite['id'], 'release_id': settings['release_id'], 'robot_id': settings['robot_id']}
            first = post('/api/v1/evaluations', request, key='evaluation-once')
            assert post('/api/v1/evaluations', request, key='evaluation-once')['id'] == first['id']
            jobs = start('jobs', '-m', 'convoy_server.evaluation_worker')
            active = wait_for(lambda: get('/api/v1/evaluations/' + first['id']),
                              lambda row: row['cases'][0]['mission_id'] is not None, timeout=30)
            first_mission = active['cases'][0]['mission_id']
            jobs.kill()
            jobs.wait(timeout=5)
            start('jobs-restarted', '-m', 'convoy_server.evaluation_worker')
            baseline = wait_for(lambda: get('/api/v1/evaluations/' + first['id']),
                                lambda row: row['report'] is not None, timeout=160)
            assert baseline['state'] == 'completed' and baseline['report']['passed'], baseline
            assert baseline['cases'][0]['mission_id'] == first_mission
            second = post('/api/v1/evaluations', request)
            repeated = wait_for(lambda: get('/api/v1/evaluations/' + second['id']),
                                lambda row: row['report'] is not None, timeout=150)
            assert repeated['state'] == 'completed' and repeated['report']['passed'], repeated
            comparison = get(f"/api/v1/evaluations/{second['id']}?baseline_id={first['id']}")
            assert comparison['success_count_delta'] == 0
            promotion = post(f"/api/v1/evaluations/{second['id']}/promote", {})
            current = get('/api/v1/robots?project_id=' + settings['project_id'])[0]
            deployment = post('/api/v1/deployments', {'robot_id': settings['robot_id'], 'release_id': settings['release_id'],
                                                     'expected_generation': current['generation']})
            ready = wait_for(lambda: get('/api/v1/deployments/' + deployment['id']), lambda row: row['state'] == 'ready')
            missions = get('/api/v1/missions?project_id=' + settings['project_id'])
            assert len(missions) == 4, 'job restart admitted an extra mission'
            evidence.update(status='passed', suite=suite, baseline=baseline, repeat=repeated, promotion=promotion,
                            deployment=ready, job_restart_preserved_mission=True, mission_count=len(missions),
                            comparison_scope=comparison['scope'], success_count_delta=comparison['success_count_delta'])
    except BaseException as error:
        evidence.update(status='failed', error_type=type(error).__name__)
        raise
    finally:
        for process in reversed(processes):
            stop(process)
        for stream in logs:
            stream.close()
        (output / 'evaluation-result.json').write_text(json.dumps(evidence, indent=2) + '\n')
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps({'status': run(args.output.resolve())['status']}))
