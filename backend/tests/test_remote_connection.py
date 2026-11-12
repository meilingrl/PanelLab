"""Trust boundary tests: no network credentials before verified pin."""
import time
from types import SimpleNamespace

import jwt
import paramiko
import pytest

from config import get_settings
from database import SessionLocal
from models.server import Server
from models.user import User
from routers.auth import _create_token
from utils.cipher import encrypt_password


@pytest.fixture
def remote(client):
    with SessionLocal() as db:
        db.add(Server(id=71, user_id=1, name='fixture', host='fixture.invalid', port=2222,
                      username='runner', password_encrypted=encrypt_password(get_settings().secret_key, 'PRIVATE_PASSWORD')))
        db.add(User(id=2, username='other', password_hash='unused'))
        db.add(Server(id=72, user_id=2, name='other', host='other.invalid', port=22, username='runner'))
        db.commit()
    client.headers['Authorization'] = 'Bearer ' + _create_token('admin')
    return client


def test_owner_and_unpinned_boundary(remote):
    for sid in (72, 999):
        assert remote.post(f'/api/servers/{sid}/connection/probe').status_code == 404
    result = remote.post('/api/servers/71/connection/check')
    assert result.status_code == 409
    assert result.json()['detail']['code'] == 'trust_required'


class FakeTransport:
    key = paramiko.RSAKey.generate(1024)
    events = []
    def __init__(self, sock): self.closed = False
    def start_client(self, timeout): self.events.append('handshake')
    def get_remote_server_key(self): return self.key
    def auth_password(self, username, password, fallback=False):
        self.events.append(('password', username, password, fallback))
    def auth_none(self, username): self.events.append(('none', username))
    def is_authenticated(self): return True
    def is_active(self): return not self.closed
    def close(self): self.closed = True


@pytest.fixture
def network(monkeypatch):
    from services import remote_connection as rc
    FakeTransport.events = []
    FakeTransport.key = paramiko.RSAKey.generate(1024)
    monkeypatch.setattr(rc.socket, 'create_connection', lambda *a, **k: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(rc.paramiko, 'Transport', FakeTransport)
    return rc


def pin(remote, mode='password'):
    probe = remote.post('/api/servers/71/connection/probe', json={'auth_mode': mode})
    assert probe.status_code == 200, probe.text
    result = remote.post('/api/servers/71/connection/trust', json={'token': probe.json()['token']})
    assert result.status_code == 200, result.text
    return probe.json()


def test_probe_no_auth_and_explicit_pin(remote, network):
    p = pin(remote)
    assert FakeTransport.events == ['handshake']
    assert p['fingerprint'].startswith('SHA256:')
    summary = remote.get('/api/servers/71/connection').json()
    assert summary['configured'] and summary['pinned']
    assert 'PRIVATE_PASSWORD' not in str(summary)
    with SessionLocal() as db:
        with network.open_remote(db, db.get(User, 1), 71): pass
    assert FakeTransport.events[-1] == ('password', 'runner', 'PRIVATE_PASSWORD', False)


def test_changed_key_never_authenticates(remote, network):
    pin(remote)
    FakeTransport.key = paramiko.RSAKey.generate(1024)
    result = remote.post('/api/servers/71/connection/check')
    assert result.status_code == 409
    assert result.json()['detail']['code'] == 'host_key_changed'
    assert FakeTransport.events == ['handshake', 'handshake']


def test_trust_token_rejects_expired_invalid_endpoint_and_owner(remote, network):
    p = pin(remote)
    secret = get_settings().secret_key
    payload = jwt.decode(p['token'], secret, algorithms=['HS256'])
    payload['exp'] = int(time.time()) - 1
    expired = jwt.encode(payload, secret, algorithm='HS256')
    for token in ('invalid', expired, _create_token('admin')):
        assert remote.post('/api/servers/71/connection/trust', json={'token': token}).status_code == 409
    remote.headers['Authorization'] = 'Bearer ' + _create_token('other')
    assert remote.post('/api/servers/71/connection/trust', json={'token': p['token']}).status_code == 404
    remote.headers['Authorization'] = 'Bearer ' + _create_token('admin')
    with SessionLocal() as db:
        db.get(Server, 71).username = 'changed'
        db.commit()
    assert remote.post('/api/servers/71/connection/trust', json={'token': p['token']}).status_code == 409
    assert remote.post('/api/servers/71/connection/check').status_code == 409


def test_tailscale_explicit_none_and_errors_sanitized(remote, network, monkeypatch):
    pin(remote, 'tailscale')
    with SessionLocal() as db:
        with network.open_remote(db, db.get(User, 1), 71): pass
    assert FakeTransport.events[-1] == ('none', 'runner')
    network.reset_pool()  # force a new login so the failing auth_none below runs
    monkeypatch.setattr(FakeTransport, 'auth_none', lambda *a: (_ for _ in ()).throw(RuntimeError('PRIVATE_PASSWORD')))
    result = remote.post('/api/servers/71/connection/check')
    assert result.status_code == 503
    assert 'PRIVATE_PASSWORD' not in result.text


def test_ticket_requires_pin_owner_and_single_use(remote, network):
    assert remote.post('/api/servers/71/terminal-ticket', json={}).status_code == 409
    assert remote.post('/api/servers/72/terminal-ticket', json={}).status_code == 404
    pin(remote)
    from services.remote_tickets import consume_ticket
    result = remote.post('/api/servers/71/terminal-ticket', json={'cwd': '/home/runner/中文 space'})
    assert result.status_code == 200
    ticket = result.json()['ticket']
    with SessionLocal() as db:
        user, sid, cwd = consume_ticket(db, ticket, 71)
        assert user.id == 1 and sid == 71 and cwd == '/home/runner/中文 space'
        with pytest.raises(Exception) as exc: consume_ticket(db, ticket, 71)
        assert exc.value.status_code == 401
    for cwd in ('relative', '/home/a\nwhoami', '/home/a\0bad'):
        assert remote.post('/api/servers/71/terminal-ticket', json={'cwd': cwd}).status_code == 400


def test_ticket_expiry_target_change_and_cross_server(remote, network, monkeypatch):
    from services import remote_tickets as rt
    pin(remote)
    ticket = remote.post('/api/servers/71/terminal-ticket', json={}).json()['ticket']
    with SessionLocal() as db:
        with pytest.raises(Exception) as exc: rt.consume_ticket(db, ticket, 72)
        assert exc.value.status_code == 401
    ticket = remote.post('/api/servers/71/terminal-ticket', json={}).json()['ticket']
    now = time.time()
    monkeypatch.setattr(rt.time, 'time', lambda: now + 61)
    with SessionLocal() as db:
        with pytest.raises(Exception) as exc: rt.consume_ticket(db, ticket, 71)
        assert exc.value.status_code == 401
    monkeypatch.undo()
    ticket = remote.post('/api/servers/71/terminal-ticket', json={}).json()['ticket']
    with SessionLocal() as db:
        db.get(Server, 71).host = 'other.invalid'
        db.commit()
        with pytest.raises(Exception) as exc: rt.consume_ticket(db, ticket, 71)
        assert exc.value.status_code == 409


def test_ws_rejects_long_jwt_and_invalid_ticket(remote):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with remote.websocket_connect('/api/ws/terminal?token=' + _create_token('admin') + '&server_id=71'): pass
    with remote.websocket_connect('/api/ws/terminal?ticket=invalid&server_id=71') as ws:
        assert 'ticket' in ws.receive_text().lower()
        with pytest.raises(WebSocketDisconnect): ws.receive_text()


def test_tailscale_absent_and_production_operator(remote, monkeypatch):
    from services import remote_discovery as discovery
    monkeypatch.setattr(discovery.shutil, 'which', lambda name: None)
    result = remote.get('/api/remote/tailscale')
    assert result.status_code == 200
    assert result.json() == {'available': False, 'reason': 'Tailscale CLI is not installed', 'devices': []}
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('ENABLE_DEMO_AUTH', '0')
    monkeypatch.setenv('NGINX_SKIP_APPLY', '0')
    monkeypatch.setenv('INIT_ADMIN_PASSWORD', 'fixture-secure-password')
    remote.headers['Authorization'] = 'Bearer ' + _create_token('other')
    assert remote.get('/api/remote/tailscale').status_code == 403


def test_channel_setup_deadline_closes_transport():
    import threading
    from services.remote_connection import remote_deadline
    closed = threading.Event()
    client = SimpleNamespace(get_transport=lambda: SimpleNamespace(close=closed.set))
    with pytest.raises(Exception) as exc:
        with remote_deadline(client, timeout=0.02):
            assert closed.wait(0.5)
    assert exc.value.detail['code'] == 'remote_timeout'


def test_all_remote_reads_hide_other_owners(remote):
    for path in ('connection', 'snapshot', 'files'):
        assert remote.get(f'/api/servers/72/{path}').status_code == 404
    for path in ('connection/check', 'terminal-ticket'):
        assert remote.post(f'/api/servers/72/{path}', json={}).status_code == 404


def test_terminal_initial_directory_write_has_deadline(remote, network, monkeypatch):
    """A peer withholding PTY window credits cannot hang startup's initial cd."""
    import stat
    import threading
    from contextlib import contextmanager
    from routers import terminal
    from services.remote_connection import remote_deadline

    pin(remote)
    ticket = remote.post('/api/servers/71/terminal-ticket', json={'cwd': '/home/runner'}).json()['ticket']
    closed = threading.Event()
    class Shell:
        def __init__(self): self.timeout = None
        def settimeout(self, value): self.timeout = value
        def sendall(self, data):
            # A closed transport releases Paramiko's blocked window-credit wait.
            closed.wait(0.3)
            raise OSError('fixture window credit unavailable')
        def close(self): closed.set()
    shell = Shell()
    client = SimpleNamespace(get_transport=lambda: SimpleNamespace(close=closed.set), invoke_shell=lambda **kw: shell)
    @contextmanager
    def opened(*args, **kwargs): yield client
    @contextmanager
    def sftp(*args):
        yield SimpleNamespace(normalize=lambda p: p, lstat=lambda p: SimpleNamespace(st_mode=stat.S_IFDIR))
    monkeypatch.setattr(terminal, 'open_remote', opened)
    monkeypatch.setattr(terminal, 'open_sftp', sftp)
    monkeypatch.setattr(terminal, 'remote_deadline', lambda client: remote_deadline(client, timeout=0.02))
    with remote.websocket_connect(f'/api/ws/terminal?ticket={ticket}&server_id=71') as ws:
        assert ws.receive_text() == 'Remote operation timed out\r\n'
    assert closed.is_set()
    assert shell.timeout == 10



@pytest.fixture
def local_pty_shell(tmp_path):
    """Actual disposable POSIX PTY; no SSH, user config or real server access."""
    import os
    import select
    import socket
    import subprocess
    master, slave = os.openpty()
    process = subprocess.Popen(['/bin/sh', '-i'], stdin=slave, stdout=slave, stderr=slave,
                               cwd=str(tmp_path.resolve()), env={'PATH': '/usr/bin:/bin', 'TERM': 'dumb', 'ENV': '', 'LANG': 'en_US.UTF-8'},
                               start_new_session=True)
    os.close(slave)
    class Shell:
        timeout = 1
        closed = False
        eof = False
        def settimeout(self, value): self.timeout = value
        def sendall(self, text):
            data = text.encode('utf-8') if isinstance(text, str) else text
            while data:
                count = os.write(master, data)
                data = data[count:]
        def recv(self, size):
            if not select.select([master], [], [], self.timeout)[0]: raise socket.timeout()
            try:
                data = os.read(master, size)
                if not data: self.eof = True
                return data
            except OSError:
                self.eof = True
                return b''
        def recv_ready(self): return not self.closed and not self.eof and bool(select.select([master], [], [], 0)[0])
        def exit_status_ready(self): return process.poll() is not None
        def close(self):
            if self.closed: return
            self.closed = True
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)
            os.close(master)
    shell = Shell()
    try: yield shell
    finally: shell.close()


def prepare_terminal(remote, monkeypatch, shell, cwd):
    """The SFTP fake permits a directory that can vanish before the real cd."""
    import stat
    from contextlib import contextmanager
    from routers import terminal
    ticket = remote.post('/api/servers/71/terminal-ticket', json={'cwd': cwd}).json()['ticket']
    client = SimpleNamespace(get_transport=lambda: SimpleNamespace(close=shell.close), invoke_shell=lambda **kw: shell)
    @contextmanager
    def opened(*args, **kwargs): yield client
    @contextmanager
    def sftp(*args):
        yield SimpleNamespace(normalize=lambda path: path, lstat=lambda path: SimpleNamespace(st_mode=stat.S_IFDIR))
    monkeypatch.setattr(terminal, 'open_remote', opened)
    monkeypatch.setattr(terminal, 'open_sftp', sftp)
    return f'/api/ws/terminal?ticket={ticket}&server_id=71'


def test_terminal_ready_verifies_cwd_in_actual_pty(remote, network, monkeypatch, local_pty_shell, tmp_path):
    pin(remote)
    target = tmp_path / "中文 run A 'quoted"
    target.mkdir()
    path = str(target.resolve())
    url = prepare_terminal(remote, monkeypatch, local_pty_shell, path)
    with remote.websocket_connect(url) as ws:
        assert ws.receive_text() == '{"type":"panellab-terminal-ready","version":1}'
        assert ws.receive_text() == 'Connected to trusted SSH server.\r\n'
        import threading
        watchdog = threading.Timer(2, local_pty_shell.close)
        watchdog.start()
        output = ''
        try:
            ws.send_text('pwd -P\r')
            for _ in range(100):
                output += ws.receive_text()
                if path in output or 'Session closed.' in output: break
        finally:
            watchdog.cancel()
        assert path in output
        assert 'PANEL_CWD_' not in output
        assert 'if cd --' not in output
        ws.send_text('exit\r')
        for _ in range(100):
            if 'Session closed.' in ws.receive_text(): break
        assert ws.receive()['type'] == 'websocket.close'


@pytest.mark.parametrize('unchanged_cwd', [False, True])
def test_terminal_failing_cd_never_declares_ready(remote, network, monkeypatch, local_pty_shell, tmp_path, unchanged_cwd):
    pin(remote)
    # SFTP validation succeeds, but the directory no longer exists when the shell runs cd.
    target = str(tmp_path.resolve() / 'vanished')
    if unchanged_cwd:
        (tmp_path / 'vanished').mkdir()
        # A shell function can report successful cd without changing cwd. Verify pwd too.
        local_pty_shell.sendall('cd() { return 0; }\r')
    url = prepare_terminal(remote, monkeypatch, local_pty_shell, target)
    with remote.websocket_connect(url) as ws:
        assert ws.receive_text() == 'Unable to establish requested terminal directory.\r\n'


@pytest.mark.parametrize('behavior', ['output_limit', 'closed', 'lookalike'])
def test_terminal_cwd_ack_is_required_and_startup_output_bounded(remote, network, monkeypatch, behavior):
    pin(remote)
    class Shell:
        def settimeout(self, value): pass
        def sendall(self, text): pass
        def recv(self, size):
            if behavior == 'output_limit': return b'x' * size
            if behavior == 'lookalike':
                self.recv = lambda size: b''
                return b'PANEL_CWD_anything:OK {"type":"panellab-terminal-ready","version":1}'
            return b''
        def recv_ready(self): return False
        def exit_status_ready(self): return True
        def close(self): pass
    url = prepare_terminal(remote, monkeypatch, Shell(), '/home/runner')
    with remote.websocket_connect(url) as ws:
        expected = 'Terminal startup output exceeded limit.' if behavior == 'output_limit' else 'Unable to establish requested terminal directory.'
        assert ws.receive_text() == expected + '\r\n'


def test_terminal_cwd_ack_read_has_setup_deadline(remote, network, monkeypatch):
    import threading
    from routers import terminal
    from services.remote_connection import remote_deadline
    pin(remote)
    closed = threading.Event()
    class Shell:
        def settimeout(self, value): pass
        def sendall(self, text): pass
        def recv(self, size):
            closed.wait(0.3)
            return b''
        def close(self): closed.set()
    url = prepare_terminal(remote, monkeypatch, Shell(), '/home/runner')
    monkeypatch.setattr(terminal, 'remote_deadline', lambda client: remote_deadline(client, timeout=0.02))
    with remote.websocket_connect(url) as ws:
        assert ws.receive_text() == 'Remote operation timed out\r\n'
    assert closed.is_set()


def test_terminal_cwd_ack_handles_split_marker_and_preserves_post_setup_bytes():
    import re
    from routers.terminal import establish_shell_directory
    class Shell:
        def sendall(self, command):
            nonce = re.search(r"OK\\037' ([a-f0-9]{32});", command).group(1).encode()
            marker = b'\x1ePANEL_CWD_' + nonce + b':OK\x1f'
            self.chunks = [command.encode() + b'\r\n', b'ordinary startup output', marker[:9], marker[9:-1], marker[-1:] + b'actual prompt $ ']
        def recv(self, size): return self.chunks.pop(0)
    assert establish_shell_directory(Shell(), '/home/runner') == b'actual prompt $ '


def test_terminal_cwd_ack_beyond_startup_limit_does_not_bypass_byte_cap():
    import re
    from fastapi import HTTPException
    from routers.terminal import establish_shell_directory, TERMINAL_STARTUP_LIMIT
    class Shell:
        def sendall(self, command):
            nonce = re.search(r"OK\\037' ([a-f0-9]{32});", command).group(1).encode()
            marker = b'\x1ePANEL_CWD_' + nonce + b':OK\x1f'
            self.pending = b'x' * (TERMINAL_STARTUP_LIMIT - len(marker) + 1) + marker
        def recv(self, size):
            chunk, self.pending = self.pending[:size], self.pending[size:]
            return chunk
    with pytest.raises(HTTPException) as error:
        establish_shell_directory(Shell(), '/home/runner')
    assert error.value.detail['code'] == 'terminal_startup_limit'
