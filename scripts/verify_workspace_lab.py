#!/usr/bin/env python3
"""Verify/serve a disposable workspace fixture, never existing servers or databases.

Build: docker build -t panellab-ssh-lab:20261103 scripts/fixtures/remote_workspace
Run with backend requirements-dev: python scripts/verify_workspace_lab.py
--serve creates fixture API on a random loopback port for manual browser checks.
--mcp-python /absolute/python also verifies optional MCP access with that interpreter.
Every container/process created here is stopped and removed on exit.
"""
from contextlib import ExitStack
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = '/home/lab/experiments/演示训练 run A'
PASSWORD = 'panellab-fixture-only'


def docker(*args):
    result = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[:1000] or 'Docker failed')
    return result.stdout.strip()


def remove_container(identifier):
    subprocess.run(['docker', 'rm', '-f', identifier], capture_output=True, timeout=30)


def stop_process(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def loopback_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serve', action='store_true', help='keep isolated fixture API alive until Ctrl-C/TERM')
    parser.add_argument('--mcp-python', help='Optional Python interpreter with scripts/requirements-mcp.txt installed')
    args = parser.parse_args()
    if args.mcp_python and not Path(args.mcp_python).is_absolute():
        parser.error('--mcp-python must be an absolute interpreter path')
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    import httpx
    import pymysql
    with ExitStack() as stack:
        fixture_parent = ROOT / 'data'
        fixture_parent.mkdir(exist_ok=True)
        local = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='workspace-lab-', dir=fixture_parent)))
        mysql = docker('run', '-d', '--label', 'panellab.purpose=workspace-verification',
                       '--publish', '127.0.0.1::3306', '--tmpfs', '/var/lib/mysql',
                       '--env', 'MYSQL_ROOT_PASSWORD=fixture-root-only',
                       '--env', 'MYSQL_DATABASE=workspace_test', '--env', 'MYSQL_USER=workspace_test',
                       '--env', f'MYSQL_PASSWORD={PASSWORD}', 'mysql:8.4.9')
        stack.callback(remove_container, mysql)
        ssh = docker('run', '-d', '--label', 'panellab.purpose=workspace-verification',
                     '--publish', '127.0.0.1::22', 'panellab-ssh-lab:20261103')
        stack.callback(remove_container, ssh)
        docker('cp', str(ROOT / 'scripts/fixtures/remote_workspace/seed.py'), f'{ssh}:/tmp/seed.py')
        docker('exec', ssh, 'python3', '/tmp/seed.py')
        docker('exec', '-d', '-u', 'lab', ssh, 'python3', FIXTURE_ROOT + '/train_demo.py')
        mysql_port = int(docker('port', mysql, '3306/tcp').rsplit(':', 1)[1])
        ssh_port = int(docker('port', ssh, '22/tcp').rsplit(':', 1)[1])
        deadline = time.monotonic() + 90
        while True:
            try:
                conn = pymysql.connect(host='127.0.0.1', port=mysql_port, user='workspace_test',
                                       password=PASSWORD, database='workspace_test', connect_timeout=2)
                conn.close()
                break
            except pymysql.MySQLError:
                if time.monotonic() > deadline:
                    raise RuntimeError('Disposable MySQL not ready') from None
                time.sleep(.25)
        api_port = loopback_port()
        env = {**os.environ, 'APP_ENV': 'test', 'ENABLE_DEMO_AUTH': 'false',
               'SECRET_KEY': 'workspace-isolated-fixture-secret-key-only',
               'INIT_ADMIN_PASSWORD': PASSWORD, 'NGINX_SKIP_APPLY': 'true',
               'DATABASE_URL': f'mysql+pymysql://workspace_test:{PASSWORD}@127.0.0.1:{mysql_port}/workspace_test'}
        subprocess.run([sys.executable, '-m', 'init_db'], cwd=ROOT / 'backend', env=env,
                       capture_output=True, check=True, timeout=30)
        log = stack.enter_context((local / 'backend.log').open('wb'))
        backend = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'main:app', '--host', '127.0.0.1',
                                    '--port', str(api_port)], cwd=ROOT / 'backend', env=env, stdout=log, stderr=log)
        stack.callback(stop_process, backend)
        client = stack.enter_context(httpx.Client(base_url=f'http://127.0.0.1:{api_port}', timeout=40, trust_env=False))
        for _ in range(100):
            try:
                if client.get('/api/health').status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if backend.poll() is not None:
                raise RuntimeError('Isolated backend stopped; inspect fixture setup')
            time.sleep(.1)
        else:
            raise RuntimeError('Isolated backend not ready')

        def request(method, path, status=200, **kwargs):
            response = client.request(method, path, **kwargs)
            assert response.status_code == status, (method, path, response.status_code, response.text[:500])
            return response

        token = request('POST', '/api/auth/login', json={'username': 'admin', 'password': PASSWORD}).json()['token']
        client.headers['Authorization'] = 'Bearer ' + token
        server = request('POST', '/api/servers', json=dict(name='隔离 Linux 测试机', host='127.0.0.1',
                         port=ssh_port, username='lab', password=PASSWORD)).json()
        sid = server['id']
        request('POST', f'/api/servers/{sid}/connection/check', status=409)
        probe = request('POST', f'/api/servers/{sid}/connection/probe', json={'auth_mode': 'password'}).json()
        trusted_fingerprints = docker('exec', ssh, 'sh', '-c', 'for f in /etc/ssh/ssh_host_*_key.pub; do ssh-keygen -l -f "$f"; done')
        assert probe['fingerprint'] in trusted_fingerprints
        request('POST', f'/api/servers/{sid}/connection/trust', json={'token': probe['token']})
        request('POST', f'/api/servers/{sid}/connection/check')
        snapshot = request('GET', f'/api/servers/{sid}/snapshot').json()
        sampling = snapshot['sampling']
        assert sampling['boot_id'] and sampling['monotonic_seconds'] > 0
        cpu_fields = {'user', 'nice', 'system', 'idle', 'iowait', 'irq', 'softirq', 'steal'}
        assert set(snapshot['system']['cpu']['ticks']) == cpu_fields
        assert all(type(value) is int and 0 <= value <= 2**53 - 1
                   for value in snapshot['system']['cpu']['ticks'].values())
        assert snapshot['system']['cpu']['percent'] is None
        assert all(type(item['ifindex']) is int and item['ifindex'] > 0
                   for item in snapshot['system']['network'])
        process = next(item for item in snapshot['processes'] if 'train_demo.py' in item['command'])
        workload = request('POST', '/api/workloads', json={
            'name': '训练现场 · 隔离演示', 'kind': 'training', 'server_id': sid,
            'root_path': FIXTURE_ROOT, 'log_path': FIXTURE_ROOT + '/train.log',
            'pid': process['pid'], 'process_identity': process['identity'],
            'notes': '真实 Linux 进程与 SFTP；日志为测试夹具，不是训练结果。'
        }).json()
        wid = workload['id']
        observed = request('GET', f'/api/workloads/{wid}/observation').json()
        assert observed['process_status'] == 'running'
        assert observed['sampling']['boot_id'] == sampling['boot_id']
        assert observed['sampling']['monotonic_seconds'] > sampling['monotonic_seconds']
        assert observed['context'] == {
            'workload': {key: workload[key] for key in
                         ('id', 'server_id', 'root_path', 'log_path', 'pid', 'process_identity')},
            'server': {key: server[key] for key in ('id', 'host', 'port', 'username')},
        }
        files = request('GET', f'/api/workloads/{wid}/files').json()
        assert any(item['name'] == 'outputs' for item in files['entries'])
        tail = request('GET', f'/api/workloads/{wid}/text', params={'path': FIXTURE_ROOT + '/large.log'}).json()
        assert tail['truncated'] and 'TAIL_MARKER' in tail['text']
        request('GET', f'/api/workloads/{wid}/text', status=403, params={'path': '/etc/passwd'})
        for name in ('outside-link/passwd', 'named-pipe'):
            response = client.get(f'/api/workloads/{wid}/text', params={'path': FIXTURE_ROOT + '/' + name})
            assert response.status_code in (400, 403, 422), (name, response.status_code)
        request('GET', f'/api/workloads/{wid}/download', status=413, params={'path': FIXTURE_ROOT + '/too-large.bin'})
        artifact = request('GET', f'/api/workloads/{wid}/download', params={'path': FIXTURE_ROOT + "/quote's result.txt"})
        assert artifact.content == b'safe quoted filename\n'
        report = request('POST', f'/api/workloads/{wid}/runs', json={
            'status': 'succeeded', 'started_at': '2026-11-03T00:00:00Z', 'finished_at': '2026-11-03T00:01:00Z',
            'exit_code': 0, 'summary': '测试用上报记录，不代表模拟训练已结束',
            'artifact_path': FIXTURE_ROOT + '/outputs/result.json'
        }).json()
        assert report['source'] == 'reported'
        request('DELETE', f'/api/servers/{sid}', status=409)
        cli_env = {**os.environ, 'PANEL_TOKEN': token}
        cli = [sys.executable, str(ROOT / 'scripts/panellab.py'), '--url', f'http://127.0.0.1:{api_port}']
        cli_observe = subprocess.run(cli + ['observe', str(wid)], env=cli_env, capture_output=True, text=True, timeout=45)
        assert cli_observe.returncode == 0, cli_observe.stderr[:500]
        assert json.loads(cli_observe.stdout)['workload_id'] == wid
        cli_download = subprocess.run(cli + ['download', str(wid), FIXTURE_ROOT + '/outputs/result.json', '--output', str(local / 'result.json')],
                                      env=cli_env, capture_output=True, text=True, timeout=45)
        assert cli_download.returncode == 0, cli_download.stderr[:500]
        expected = b'{"fixture": true, "score": 0.42}\n'
        assert hashlib.sha256((local / 'result.json').read_bytes()).digest() == hashlib.sha256(expected).digest()
        # CLI-created objects must be visible through the same UI-facing API.
        cli_task_file = local / 'agent-task.json'
        cli_task_file.write_text(json.dumps({
            'name': '每日早报 · CLI 登记', 'kind': 'scheduled', 'server_id': sid,
            'root_path': FIXTURE_ROOT, 'log_path': FIXTURE_ROOT + '/train.log',
            'schedule_note': '每日 08:00（说明，未安装调度）',
            'notes': 'CLI创建的测试对象；没有运行或安装早报任务。'
        }, ensure_ascii=False))
        cli_task = subprocess.run(cli + ['tasks', 'create', '--json', str(cli_task_file)],
                                  env=cli_env, capture_output=True, text=True, timeout=45)
        assert cli_task.returncode == 0, cli_task.stderr[:500]
        agent_task_id = json.loads(cli_task.stdout)['id']
        assert request('GET', f'/api/workloads/{agent_task_id}').json()['name'] == '每日早报 · CLI 登记'
        # A real PTY starts at the chosen directory; the ticket cannot be reused.
        from websockets.sync.client import connect
        ticket = request('POST', f'/api/servers/{sid}/terminal-ticket', json={'cwd': FIXTURE_ROOT}).json()['ticket']
        ws_url = f'ws://127.0.0.1:{api_port}/api/ws/terminal?server_id={sid}&ticket={ticket}'
        with connect(ws_url, open_timeout=10, proxy=None) as ws:
            first = ws.recv(timeout=15)
            assert 'Connected' in first or 'ready' in first
            ws.send("printf 'PANEL_WORKSPACE_CWD=%s\\n' \"$PWD\"\r")
            seen = ''
            for _ in range(30):
                seen += ws.recv(timeout=5)
                if 'PANEL_WORKSPACE_CWD=' + FIXTURE_ROOT in seen:
                    break
            assert 'PANEL_WORKSPACE_CWD=' + FIXTURE_ROOT in seen
        with connect(ws_url, open_timeout=10, proxy=None) as ws:
            rejected = ws.recv(timeout=5)
            assert 'Connected' not in rejected and 'ready' not in rejected
        # Rotate only the disposable container's key and require a fresh explicit pin.
        docker('exec', ssh, 'python3', '-c',
               "from pathlib import Path; p=Path('/etc/ssh/ssh_host_ed25519_key'); p.unlink(); p.with_suffix('.pub').unlink()")
        docker('exec', ssh, 'ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', '/etc/ssh/ssh_host_ed25519_key')
        docker('kill', '--signal=HUP', ssh)
        for _ in range(30):
            changed = client.post(f'/api/servers/{sid}/connection/check')
            if changed.status_code == 409:
                break
            time.sleep(.1)
        assert changed.status_code == 409 and changed.json()['detail']['code'] == 'host_key_changed'
        replacement = request('POST', f'/api/servers/{sid}/connection/probe', json={'auth_mode': 'password'}).json()
        key_line = docker('exec', ssh, 'ssh-keygen', '-l', '-f', '/etc/ssh/ssh_host_ed25519_key.pub')
        assert replacement['fingerprint'] in key_line
        request('POST', f'/api/servers/{sid}/connection/trust', json={'token': replacement['token']})
        request('POST', f'/api/servers/{sid}/connection/check')
        print('PASS: real Linux SSH host trust, process identity, pin task, SFTP browse/tail, confinement, download, reported run, server guard, shared CLI-created tasks, real PTY/replay and changed-host-key refusal', flush=True)
        if args.mcp_python:
            mcp_check = subprocess.run([
                args.mcp_python, str(ROOT / 'scripts/verify_mcp_lab.py'),
                '--url', f'http://127.0.0.1:{api_port}', '--task-id', str(wid),
                '--server-id', str(sid), '--root', FIXTURE_ROOT,
            ], env=cli_env, capture_output=True, text=True, timeout=120)
            if mcp_check.returncode:
                # Fixture diagnostics can include tool data; never echo the token.
                diagnostic = (mcp_check.stderr + mcp_check.stdout).replace(token, '[redacted]')
                raise RuntimeError('MCP fixture verification failed: ' + diagnostic[-3000:])
            print(mcp_check.stdout.strip(), flush=True)
        if args.serve:
            print(json.dumps({'api_url': f'http://127.0.0.1:{api_port}', 'username': 'admin',
                              'fixture_password': PASSWORD, 'server_id': sid, 'workload_id': wid,
                              'ssh_container': ssh, 'mysql_container': mysql}, ensure_ascii=False), flush=True)
            print('Serving only disposable fixture; Ctrl-C or TERM cleans it up.', flush=True)
            while True:
                time.sleep(1)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
