#!/usr/bin/env python3
"""Verify site changes against disposable MySQL + Linux Nginx, never existing services.

Run with backend requirements-dev installed and Docker available:
    python scripts/verify_site_change_lab.py
Creates labeled containers on loopback random ports and removes only those containers
in finally. Requires locally available nginx:stable-alpine and mysql:8.4.9 images.
The temporary MySQL password below is exclusively for these disposable fixtures.
"""
import os
import sys
import time
import uuid
import tempfile
import subprocess
import shlex
from contextlib import ExitStack
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'backend'))

def docker(*args):
    result = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'Docker command failed')
    return result.stdout.strip()

def remove_container(container_id):
    subprocess.run(['docker', 'rm', '-f', container_id], capture_output=True, timeout=30)

with ExitStack() as stack:
    # Use the repository's ignored data directory: macOS system temp paths may
    # not be shared with Docker/Colima. Never mount an existing user's service.
    fixture_parent = root / 'data'
    fixture_parent.mkdir(exist_ok=True)
    lab = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='panellab-lab-', dir=fixture_parent))).resolve()
    (lab / 'conf.d').mkdir()
    for version in ('a', 'b'):
        (lab / 'www' / version).mkdir(parents=True)
        (lab / 'www' / version / 'index.html').write_text(f'PanelLab version {version.upper()}\n')
    (lab / 'nginx.conf').write_text(
        'pid /tmp/nginx.pid;\nerror_log /dev/stderr notice;\n'
        'events { worker_connections 128; }\n'
        'http { access_log off; include /fixture/conf.d/*.conf; }\n')
    mysql_id = docker('run', '-d', '--label', 'panellab.purpose=isolated-verification',
        '--publish', '127.0.0.1::3306', '--tmpfs', '/var/lib/mysql',
        '--env', 'MYSQL_ROOT_PASSWORD=panellab-isolated-root-only',
        '--env', 'MYSQL_DATABASE=panellab_test', '--env', 'MYSQL_USER=panellab_test',
        '--env', 'MYSQL_PASSWORD=panellab-isolated-test-only', 'mysql:8.4.9')
    stack.callback(remove_container, mysql_id)
    nginx_id = docker('run', '-d', '--label', 'panellab.purpose=isolated-verification',
        '--publish', '127.0.0.1::8080', '--mount', f'type=bind,src={lab},dst=/fixture',
        'nginx:stable-alpine', 'nginx', '-c', '/fixture/nginx.conf', '-g', 'daemon off;')
    stack.callback(remove_container, nginx_id)
    mysql_port = int(docker('port', mysql_id, '3306/tcp').rsplit(':', 1)[1])
    http_port = int(docker('port', nginx_id, '8080/tcp').rsplit(':', 1)[1])
    import pymysql
    deadline = time.monotonic() + 60
    while True:
        try:
            connection = pymysql.connect(host='127.0.0.1', port=mysql_port,
                user='panellab_test', password='panellab-isolated-test-only',
                database='panellab_test', connect_timeout=2, read_timeout=2)
            connection.close()
            break
        except pymysql.MySQLError:
            if time.monotonic() >= deadline:
                raise RuntimeError('Disposable MySQL did not become ready') from None
            time.sleep(.25)
    os.environ.update(APP_ENV='test', ENABLE_DEMO_AUTH='false',
        SECRET_KEY='panellab-isolated-integration-secret-key-only',
        DATABASE_URL=f'mysql+pymysql://panellab_test:panellab-isolated-test-only@127.0.0.1:{mysql_port}/panellab_test?connect_timeout=3&read_timeout=5',
        NGINX_CONF_DIR=str(lab / 'conf.d'), NGINX_SKIP_APPLY='false',
        NGINX_TEST_CMD=f'docker exec {nginx_id} nginx -t -c /fixture/nginx.conf',
        NGINX_RELOAD_CMD=f'docker exec {nginx_id} nginx -s reload -c /fixture/nginx.conf')
    from fastapi.testclient import TestClient
    from main import app
    from database import SessionLocal
    from models.user import User
    import bcrypt, httpx

    def check(r, status=200):
        assert r.status_code == status, (r.status_code, r.text[:300])
        return r.json()

    def serve(expected):
        for _ in range(30):
            try:
                response = httpx.get(f'http://127.0.0.1:{http_port}/', headers={'Host': 'lab.example.test'}, timeout=2, trust_env=False)
                if expected in response.text: return
            except httpx.HTTPError: pass
            time.sleep(.1)
        raise AssertionError('Expected HTTP version was not served')

    with TestClient(app) as client:
        username = 'lab_' + uuid.uuid4().hex[:8]
        with SessionLocal() as db:
            db.add(User(username=username, password_hash=bcrypt.hashpw(b'lab-password-only', bcrypt.gensalt()).decode()))
            db.commit()
        token = check(client.post('/api/auth/login', json={'username': username, 'password': 'lab-password-only'}))['token']
        headers={'Authorization': 'Bearer ' + token}
        config=dict(name=username, domain='lab.example.test', site_type='static', root_path='/fixture/www/a', proxy_target='', listen_port=8080, enabled=True)
        def preview(operation, site_id=None, candidate=None):
            body={'operation': operation}
            if site_id is not None: body['site_id']=site_id
            if candidate is not None: body['config']=candidate
            return check(client.post('/api/site-changes/preview', headers=headers, json=body))
        def execute(plan, expected=200):
            return check(client.post('/api/site-changes/execute', headers=headers, json={'token': plan['token']}), expected)
        before=list((lab/'conf.d').glob('*.conf'))
        plan=preview('create', candidate=config)
        assert list((lab/'conf.d').glob('*.conf')) == before
        result=execute(plan); site_id=result['site']['id']
        assert result['change']['status']=='applied'
        config_file=lab/'conf.d'/result['site']['config_filename']
        serve('version A')
        execute(plan,409)
        changed={**config,'root_path':'/fixture/www/b'}
        plan=preview('update',site_id,changed)
        result=execute(plan); assert result['change']['status']=='applied'; serve('version B')
        plan=preview('update',site_id,config)
        drift=lab/'conf.d'/'external.conf'; drift.write_text('# changed outside PanelLab\n')
        execute(plan,409); serve('version B'); drift.unlink()
        # Nginx real parser fails while target file must restore and DB remains B.
        drift.write_text('invalid_nginx_directive;\n')
        plan=preview('update',site_id,config)
        failed=execute(plan,503)
        assert failed['detail']['status']=='failed_restored'
        site=check(client.get(f'/api/sites/{site_id}',headers=headers))
        assert site['root_path']=='/fixture/www/b'; serve('version B'); drift.unlink()
        # Fail one reload command, then allow the recovery reload to reach real Nginx.
        reload_command = os.environ['NGINX_RELOAD_CMD']
        wrapper = lab / 'reload_once.py'
        wrapper.write_text(
            "import pathlib, subprocess, sys\n"
            "flag=pathlib.Path(__file__).with_suffix('.seen')\n"
            "if not flag.exists():\n    flag.write_text('failed once')\n    sys.exit(1)\n"
            f"sys.exit(subprocess.run({shlex.split(reload_command)!r}).returncode)\n")
        os.environ['NGINX_RELOAD_CMD'] = shlex.join([sys.executable, str(wrapper)])
        failed = execute(preview('update', site_id, config), 503)
        assert failed['detail']['status'] == 'failed_restored'
        serve('version B')
        # Recovery failure must remain visibly different from successful restoration.
        os.environ['NGINX_RELOAD_CMD'] = shlex.join([sys.executable, '-c', 'raise SystemExit(1)'])
        failed = execute(preview('update', site_id, config), 503)
        assert failed['detail']['status'] == 'recovery_failed'
        os.environ['NGINX_RELOAD_CMD'] = reload_command
        serve('version B')
        # A permitted >64 KiB legacy config must be retained in full in MySQL audit.
        original=config_file.read_text()
        config_file.write_text(original + '# audit padding\n' * 5000)
        expected_before=config_file.read_text()
        assert len(expected_before.encode()) > 65535
        plan=preview('delete',site_id)
        result=execute(plan); assert result['change']['status']=='applied'
        assert result['change']['before']==expected_before
        check(client.get(f'/api/sites/{site_id}',headers=headers),404)
        history=check(client.get('/api/site-changes',headers=headers))['items']
        assert any(x['status']=='failed_restored' for x in history)
        assert any(x['status']=='recovery_failed' for x in history)
        assert len(history)>=6
        print('PASS: real MySQL + Linux Nginx create A, update B, replay rejection, external drift rejection, syntax-failure restore, reload recovery, explicit recovery failure, large-config audit, delete and retained history')
