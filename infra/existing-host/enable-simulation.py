#!/usr/bin/env python3
"""Enable managed simulations on the existing Convoy Compose host (run as root).

Adds no cloud resources. Keeps execution signing outside the shared data mount;
only the API receives that mount. Retains a backup before changing configuration.
The evaluation process has bounded memory/logs and uses the existing database.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import yaml

KEYGEN = '''import json,os,uuid
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from convoy_contracts.grants import SigningKeys
p=Path('/keys/keys.json')
if not p.exists():
 active,keys={},[]
 for purpose in ('action','planner'):
  kid=purpose+'-'+uuid.uuid4().hex;active[purpose]=kid
  key=Ed25519PrivateKey.generate()
  keys.append({'kid':kid,'purpose':purpose,'audience':'convoy-hosted:'+purpose,'private_key_pem':key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode()})
 fd=os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
 with os.fdopen(fd,'w') as f:
  json.dump({'schema_version':1,'issuer':'https://deployconvoy.com','active':active,'keys':keys},f);f.flush();os.fsync(f.fileno())
s=SigningKeys(p)
print(json.dumps({purpose:s.verification_document(purpose) for purpose in ('action','planner')}))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-dir', type=Path, default=Path('/opt/convoy'))
    args = parser.parse_args()
    root = args.app_dir.resolve()
    compose = root / 'compose.yaml'
    env = root / 'portal/control-plane.env'
    document = yaml.safe_load(compose.read_text())
    services = document['services']
    api = services['control-plane']
    if api.get('volumes', [None])[0] != './portal/data:/data':
        raise ValueError('Expected existing-host data mount')
    values = dict(line.split('=', 1) for line in env.read_text().splitlines() if '=' in line and not line.startswith('#'))
    if values.get('CONVOY_EXECUTION_SECRET') or values.get('CONVOY_PLANNER_EXECUTION_SECRET'):
        raise ValueError('Legacy authority requires an explicit migration')
    signing = values.get('CONVOY_EXECUTION_SIGNING_KEYS_FILE', '').strip("\"'")
    if signing and signing != '/run/execution-signing/keys.json':
        raise ValueError('Existing signing configuration must be retained')
    backup = root / 'rollback' / ('simulation-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    backup.mkdir(parents=True, mode=0o700)
    shutil.copy2(compose, backup / 'compose.yaml')
    shutil.copy2(env, backup / 'control-plane.env')
    os.chmod(backup / 'control-plane.env', 0o600)
    uid = int(subprocess.check_output(['docker','compose','exec','-T','control-plane','id','-u'], cwd=root, text=True))
    keydir = root / 'portal/execution-signing'
    if signing and not (keydir / 'keys.json').is_file():
        raise ValueError('Established signing key is missing; refusing replacement')
    keydir.mkdir(mode=0o700, exist_ok=True)
    os.chown(keydir, uid, uid)
    public = json.loads(subprocess.check_output(['docker','run','--rm','-i','--network','none','--entrypoint','python',
        '--mount',f'type=bind,src={keydir},dst=/keys',api['image'],'-'], input=KEYGEN, text=True))
    publicdir = root / 'portal/execution-verification'
    publicdir.mkdir(mode=0o755, exist_ok=True)
    for purpose, value in public.items():
        path = publicdir / (purpose + '.json')
        path.write_text(json.dumps(value))
        os.chmod(path, 0o444)
    mount = './portal/execution-signing:/run/execution-signing:ro'
    if mount not in api['volumes']:
        api['volumes'].append(mount)
    additions = {'CONVOY_EXECUTION_SIGNING_KEYS_FILE':'/run/execution-signing/keys.json',
                 'CONVOY_SIMULATOR':'1', 'CONVOY_RECORDING_QUOTA_BYTES':str(512*1024**2)}
    lines = [line for line in env.read_text().splitlines() if line.split('=',1)[0] not in additions]
    temporary = env.with_suffix('.env.tmp')
    temporary.write_text('\n'.join(lines + [k+'='+v for k,v in additions.items()]) + '\n')
    os.chmod(temporary, 0o600)
    temporary.replace(env)
    services['evaluations'] = {
        'image':api['image'], 'restart':'unless-stopped', 'init':True, 'stop_grace_period':'35s',
        'command':['python','-m','convoy_server.evaluation_worker'],
        'environment':{'CONVOY_DATA_DIR':'/data','CONVOY_SIMULATOR':'1','CONVOY_LOG_LEVEL':'WARNING',
                       'CONVOY_SQLITE_WAL': values.get('CONVOY_SQLITE_WAL','0').strip("\"'")},
        'volumes':['./portal/data:/data'], 'mem_limit':'192m', 'cpus':'0.5',
        'security_opt':['no-new-privileges:true'],
        'logging':{'driver':'json-file','options':{'max-size':'5m','max-file':'2'}},
        'healthcheck':{'test':['CMD','python','-c','from convoy_server.db import init_engine; init_engine().connect().close()'],
                       'interval':'30s','timeout':'5s','retries':3},
    }
    tmp = compose.with_suffix('.simulation-tmp')
    tmp.write_text(yaml.safe_dump(document, sort_keys=False))
    os.chmod(tmp, 0o600)
    tmp.replace(compose)
    subprocess.run(['docker','compose','config','--quiet'], cwd=root, check=True)
    subprocess.run(['docker','compose','up','-d','--no-deps','control-plane','evaluations'], cwd=root, check=True)
    print(json.dumps({'configured':True,'backup':str(backup),'recording_quota_mib':512,'new_cloud_resources':0}))


if __name__ == '__main__':
    main()
