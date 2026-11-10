"""Agent client: one JSON line over a pinned SSH exec channel, mapped errors, install bundle."""
import io
import json
import shlex
import tarfile
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services import agent_client

CTL = '/usr/local/bin/panellab-agentctl'


class Channel:
    def __init__(self, stdout=b'', stderr=b'', status=0, hang=False):
        self.stdout, self.stderr, self.status, self.hang = stdout, stderr, status, hang
        self.sent = b''
        self.command = None
        self.write_shutdown = False
        self.closed = False

    def settimeout(self, value): pass
    def exec_command(self, command): self.command = command
    def sendall(self, data): self.sent += data
    def shutdown_write(self): self.write_shutdown = True
    def recv_ready(self): return bool(self.stdout)
    def recv_stderr_ready(self): return bool(self.stderr)
    def recv(self, n): out, self.stdout = self.stdout[:n], self.stdout[n:]; return out
    def recv_stderr(self, n): out, self.stderr = self.stderr[:n], self.stderr[n:]; return out
    def exit_status_ready(self): return not self.hang and not self.stdout and not self.stderr
    def recv_exit_status(self): return self.status
    def close(self): self.closed = True


class FakeSFTP:
    def __init__(self): self.files = {}
    def normalize(self, path): return '/home/runner' if path == '.' else path
    def putfo(self, fl, path, *a, **k): self.files[path] = fl.read(); return SimpleNamespace(st_size=len(self.files[path]))
    def get_channel(self): return SimpleNamespace(settimeout=lambda v: None)
    def __enter__(self): return self
    def __exit__(self, *a): return False


def install(monkeypatch, channels, sftp=None):
    """Patch open_remote to yield a client whose sessions are the given channels in order."""
    channels = list(channels)
    transport = SimpleNamespace(open_session=lambda **kw: channels.pop(0), close=lambda: None)
    client = SimpleNamespace(get_transport=lambda: transport, open_sftp=lambda: sftp)

    @contextmanager
    def fake_open(db, user, server_id):
        yield client
    monkeypatch.setattr(agent_client, 'open_remote', fake_open)


def code_of(exc):
    return exc.value.detail['code'], exc.value.status_code


def ok(result):
    return json.dumps({'ok': True, 'result': result}).encode() + b'\n'


def err(code, message='msg'):
    return json.dumps({'ok': False, 'error': {'code': code, 'message': message}}).encode() + b'\n'


def test_call_sends_one_json_line_and_parses_result(monkeypatch):
    ch = Channel(stdout=ok({'jobs': []}))
    install(monkeypatch, [ch])
    assert agent_client.call(None, None, 1, 'jobs.list', {'x': 1}) == {'jobs': []}
    assert ch.command == CTL
    assert ch.sent.endswith(b'\n') and ch.sent.count(b'\n') == 1
    assert json.loads(ch.sent) == {'protocol_version': 1, 'op': 'jobs.list', 'args': {'x': 1}}
    assert ch.write_shutdown and ch.closed
    ch2 = Channel(stdout=ok({}))
    install(monkeypatch, [ch2])
    agent_client.call(None, None, 1, 'status')
    assert json.loads(ch2.sent)['args'] == {}


def test_maps_exit_codes_127_3_4(monkeypatch):
    for status, stderr, expect in [
        (127, b'bash: panellab-agentctl: command', ('agent_not_installed', 404)),
        (1, b'x: not found', ('agent_not_installed', 404)),
        (3, b'{"code":"agent_unreachable","message":"RAWSECRET"}', ('agent_unreachable', 503)),
        (4, b'{"code":"agent_permission","message":"RAWSECRET"}', ('agent_permission', 403)),
        (2, b'{"code":"bad_input","message":"RAWSECRET"}', ('agent_protocol', 502)),
    ]:
        install(monkeypatch, [Channel(stdout=b'RAWSECRET', stderr=stderr, status=status)])
        with pytest.raises(HTTPException) as exc:
            agent_client.call(None, None, 1, 'status')
        assert code_of(exc) == expect
        assert 'RAWSECRET' not in json.dumps(exc.value.detail)
    install(monkeypatch, [Channel(status=4)])
    with pytest.raises(HTTPException) as exc:
        agent_client.call(None, None, 1, 'status')
    assert '请重新登录 SSH 或确认已加入 panellab 组' in exc.value.detail['message']


def test_maps_agent_errors_conflict_409_invalid_422(monkeypatch):
    for code, status in [('conflict', 409), ('invalid', 422), ('bad_request', 422), ('not_found', 404),
                         ('draining', 503), ('internal', 502), ('protocol_mismatch', 409)]:
        install(monkeypatch, [Channel(stdout=err(code, '细节'))])
        with pytest.raises(HTTPException) as exc:
            agent_client.call(None, None, 1, 'status')
        assert code_of(exc) == (code, status)
    assert '请升级服务器上的 Agent' in exc.value.detail['message']
    install(monkeypatch, [Channel(stdout=err('conflict', '版本冲突'))])
    with pytest.raises(HTTPException) as exc:
        agent_client.call(None, None, 1, 'status')
    assert exc.value.detail['message'] == '版本冲突'


def test_output_over_2mib_fails(monkeypatch):
    install(monkeypatch, [Channel(stdout=b'x' * (2 * 1024 * 1024 + 1))])
    with pytest.raises(HTTPException) as exc:
        agent_client.call(None, None, 1, 'status')
    assert code_of(exc) == ('agent_output_limit', 502)
    ch = Channel(stdout=ok({'pad': 'x' * 100000}))
    install(monkeypatch, [ch])
    assert len(agent_client.call(None, None, 1, 'status')['pad']) == 100000


def test_non_utf8_output_is_protocol_error_not_500(monkeypatch):
    for out in (b'\xff\xfe{"ok": true}', b'not json', b'[1]', b'{"ok": true}', b''):
        install(monkeypatch, [Channel(stdout=out)])
        with pytest.raises(HTTPException) as exc:
            agent_client.call(None, None, 1, 'status')
        assert code_of(exc) == ('agent_protocol', 502)
        assert exc.value.detail['message'] == '服务器 Agent 返回了无法识别的响应'


def test_stall_hits_deadline_and_test_email_gets_longer(monkeypatch):
    seen = []

    @contextmanager
    def deadline(client, timeout=10):
        seen.append(timeout)
        yield
    monkeypatch.setattr(agent_client, 'remote_deadline', deadline)
    for op in ('status', 'alerts.test_email'):
        install(monkeypatch, [Channel(stdout=ok({}))])
        agent_client.call(None, None, 1, op)
    assert seen == [30, 130]
    install(monkeypatch, [Channel(hang=True)])
    ticks = iter([0, 0, 31, 31, 31])
    monkeypatch.setattr(agent_client.time, 'monotonic', lambda: next(ticks, 99))
    with pytest.raises(HTTPException) as exc:
        agent_client.call(None, None, 1, 'status')
    assert code_of(exc) == ('agent_timeout', 503)


def test_secrets_not_in_errors(monkeypatch):
    install(monkeypatch, [Channel(stdout=err('invalid', '作业无效'))])
    with pytest.raises(HTTPException) as exc:
        agent_client.call(None, None, 1, 'jobs.create', {'job': {}, 'secret_env': {'K': 'TOPSECRET'}})
    assert 'TOPSECRET' not in json.dumps(exc.value.detail)


def test_upload_bundle_excludes_tests_and_quotes_path(monkeypatch):
    sftp = FakeSFTP()
    runner = Channel(status=0)
    install(monkeypatch, [runner], sftp)
    result = agent_client.upload_bundle(None, None, 1)
    version = agent_client.agent_version()
    d = '/home/runner/panellab-agent-' + version
    assert result == {'path': d, 'command': 'sudo bash ' + shlex.quote(d + '/install.sh'), 'version': version}
    tar_path = d + '.tar'
    assert list(sftp.files) == [tar_path]
    blob = sftp.files[tar_path]
    assert len(blob) < 2 * 1024 * 1024
    with tarfile.open(fileobj=io.BytesIO(blob)) as tf:
        members = tf.getmembers()
        names = [m.name for m in members]
        assert names == sorted(names)
        assert 'install.sh' in names and 'systemd/panellab-agent.service' in names
        assert 'panellab_agent/daemon.py' in names
        for n in names:
            assert not n.startswith('tests') and '__pycache__' not in n and not n.endswith('.pyc')
            assert n != 'pytest.ini' and not any(p.startswith('tmp') for p in n.split('/'))
        for m in members:
            assert (m.uid, m.gid, m.uname, m.gname, m.mtime) == (0, 0, 'root', 'root', 0)
            if m.isfile():
                assert m.mode == (0o755 if m.name == 'install.sh' else 0o644)
    assert agent_client.build_bundle() == agent_client.build_bundle()
    assert runner.command == ('mkdir -p %s && tar -xf %s -C %s && rm -f %s'
                              % tuple(shlex.quote(p) for p in (d, tar_path, d, tar_path)))


def test_upload_bundle_quotes_hostile_home(monkeypatch):
    sftp = FakeSFTP()
    sftp.normalize = lambda p: "/home/o'brien $(x)"
    runner = Channel()
    install(monkeypatch, [runner], sftp)
    result = agent_client.upload_bundle(None, None, 1)
    assert shlex.quote("/home/o'brien $(x)/panellab-agent-" + agent_client.agent_version()) in runner.command
    assert result['command'].startswith("sudo bash '")


def test_upload_bundle_extract_failure(monkeypatch):
    install(monkeypatch, [Channel(status=2, stderr=b'tar: SECRETPATH')], FakeSFTP())
    with pytest.raises(HTTPException) as exc:
        agent_client.upload_bundle(None, None, 1)
    assert code_of(exc) == ('agent_install_failed', 502)
    assert 'SECRETPATH' not in json.dumps(exc.value.detail)


def test_version_parsed_not_imported():
    assert agent_client.agent_version() == '0.1.0'
