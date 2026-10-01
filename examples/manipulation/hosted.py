"""Enroll and run a qualified local model pair against a hosted Convoy workspace.

Uses the existing model registry without downloads. Enrollment uses a temporary
user session; serving uses the device identity and public verification keys only.
No deployment or mission starts implicitly.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
SOURCES = [ROOT / p for p in ('control-plane/agent', 'control-plane/contracts', 'control-plane/server',
    'control-plane/worker', 'integrations/simulation/src', 'integrations/lerobot/src', 'integrations/planner/src')]
sys.path[:0] = list(map(str, SOURCES))


def write(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def enroll(args):
    import httpx
    from convoy_agent.agent import AgentConfig
    from convoy_agent.agent import enroll as enroll_device
    from convoy_contracts.pairing import PAIRED_PROFILE
    from convoy_lerobot.local_recipe import load_registry

    registry = args.registry.resolve(strict=True)
    load_registry(registry)
    recipes = json.loads(registry.read_text())['recipes']
    server = args.server.rstrip('/')
    parsed = urlsplit(server)
    if parsed.scheme != 'https' or parsed.netloc != parsed.hostname or parsed.path or parsed.query or parsed.fragment:
        raise ValueError('Use a credential-free HTTPS workspace origin')
    state = args.state.resolve()
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    if (state / 'connection.json').exists():
        raise ValueError('Runner already enrolled; use serve with existing state')
    setup = state / 'setup.json'
    if setup.exists():
        marker = json.loads(setup.read_text())
        if marker['server'] != server or marker['registry'] != str(registry):
            raise ValueError('Resume requires the original origin and registry')
    else:
        marker = {'server': server, 'registry': str(registry), 'id': uuid.uuid4().hex}
        write(setup, marker)
    credentials = json.loads(args.login_file.read_text())
    with httpx.Client(base_url=server, timeout=30, follow_redirects=False, trust_env=False,
                     headers={'Origin': server, 'X-Convoy-Client': 'web'}) as client:
        response = client.post('/api/platform/auth/login', json=credentials)
        response.raise_for_status()

        def post(path, body, operation):
            response = client.post('/api/platform/' + path, json=body,
                                   headers={'Idempotency-Key': marker['id'] + '-' + operation})
            response.raise_for_status()
            return response.json()

        try:
            project = post('projects', {'name': 'Simulation lab · Mac runner'}, 'project')
            cfg = AgentConfig(state / 'robot')
            if not cfg.credential:
                token_path = state / 'enrollment.json'
                if not token_path.exists():
                    write(token_path, post('enrollments', {'label':'Hosted simulation · Mac', 'simulated':True}, 'enrollment'))
                token = json.loads(token_path.read_text())['token']
                enroll_device(state / 'robot', server=server, token=token, name='Virtual Sawyer · Mac', simulate=True)
                cfg = AgentConfig(state / 'robot')
            robot = post('robots', {'project_id':project['id'], 'device_id':cfg.data['device_id'],
                                   'name':'Virtual Sawyer · Mac (offline simulation)', 'profile':PAIRED_PROFILE}, 'robot')
            app = post('applications', {'project_id':project['id'], 'name':'Qwen + SmolVLA · puck pick-and-place'}, 'application')
            releases = [post(f"applications/{app['id']}/releases", {'manifest':r['manifest']}, 'release-'+str(i))
                        for i, r in enumerate(recipes)]
            write(state / 'probes.json', {key:secrets.token_urlsafe(48) for key in ('CONVOY_WORKER_PROBE_TOKEN','CONVOY_PLANNER_PROBE_TOKEN')})
            write(state / 'connection.json', {'server':server,'registry':str(registry), 'project_id':project['id'],
                'robot_id':robot['id'], 'device_id':cfg.data['device_id'], 'application_id':app['id'],
                'releases':releases, 'compute':'This Mac; offline lockstep simulation; keep runner awake'})
            print('Simulator and releases registered. Deployment and mission start remain explicit.')
        finally:
            client.post('/api/platform/auth/logout', json={})


def serve(args):
    from convoy_contracts.grants import GrantVerifier

    state = args.state.resolve(strict=True)
    connection = json.loads((state / 'connection.json').read_text())
    verification = args.verification.resolve(strict=True)
    for purpose in ('action','planner'):
        GrantVerifier(verification / (purpose + '.json'), purpose=purpose)
    env = {k:os.environ[k] for k in ('PATH','HOME','TMPDIR','LANG','LC_ALL') if k in os.environ}
    env.update(PYTHONPATH=os.pathsep.join(map(str,SOURCES)), PYTHONUNBUFFERED='1', HF_HUB_OFFLINE='1',
               TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1', TOKENIZERS_PARALLELISM='false')
    env.update(json.loads((state / 'probes.json').read_text()))
    env.update({f'CONVOY_{purpose.upper()}_VERIFICATION_KEYS_FILE':str(verification / (purpose+'.json'))
                for purpose in ('action','planner')})
    stop = threading.Event()
    for sig in (signal.SIGINT,signal.SIGTERM):
        signal.signal(sig, lambda *_:stop.set())
    children,logs = [],[]
    try:
        for name,module,extra in (
            ('coordinator','convoy_agent.coordinator.paired',['--local-registry',connection['registry'],
                '--poll-seconds','1','--clock-uncertainty-seconds','2']),
            ('recordings','convoy_agent.coordinator.recordings',[]),
        ):
            log = (state / (name+'.log')).open('ab')
            logs.append(log)
            child = subprocess.Popen([sys.executable,'-m',module,'--data-dir',str(state/'robot'),
                '--robot-id',connection['robot_id'],*extra],env=env,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
            children.append(child)
        print('Hosted simulator runner started; awaiting deployments and missions.',flush=True)
        while not stop.wait(2):
            if any(child.poll() is not None for child in children):
                raise RuntimeError('A runner process exited; inspect its private logs')
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
        for child in reversed(children):
            try:
                child.wait(timeout=40)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
        for log in logs:
            log.close()


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command',required=True)
    setup = sub.add_parser('enroll')
    setup.add_argument('--server',required=True)
    setup.add_argument('--state',type=Path,required=True)
    setup.add_argument('--registry',type=Path,required=True)
    setup.add_argument('--login-file',type=Path,required=True,help='Private JSON with email and password; enrollment only')
    runner = sub.add_parser('serve')
    runner.add_argument('--state',type=Path,required=True)
    runner.add_argument('--verification',type=Path,required=True)
    args = parser.parse_args()
    (enroll if args.command=='enroll' else serve)(args)


if __name__ == '__main__':
    main()
