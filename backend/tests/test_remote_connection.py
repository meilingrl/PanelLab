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
    def opened(*args): yield client
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


def test_terminal_ready_frame_follows_auth_shell_and_cwd_setup(remote, network, monkeypatch):
    """The first control frame cannot precede successful SSH/PTTY/cwd setup."""
    import stat
    from contextlib import contextmanager
    from routers import terminal
    pin(remote)
    ticket = remote.post('/api/servers/71/terminal-ticket', json={'cwd': '/home/runner'}).json()['ticket']
    events = []
    class Shell:
        def settimeout(self, value): events.append(('timeout', value))
        def sendall(self, text): events.append(('write', text))
        def recv_ready(self): return False
        def exit_status_ready(self): return True
        def close(self): pass
    def shell(**kwargs):
        events.append('shell')
        return Shell()
    client = SimpleNamespace(get_transport=lambda: SimpleNamespace(close=lambda: None), invoke_shell=shell)
    @contextmanager
    def opened(*args):
        events.append('authenticated')
        yield client
    @contextmanager
    def sftp(*args):
        yield SimpleNamespace(normalize=lambda path: path, lstat=lambda path: SimpleNamespace(st_mode=stat.S_IFDIR))
    monkeypatch.setattr(terminal, 'open_remote', opened)
    monkeypatch.setattr(terminal, 'open_sftp', sftp)
    with remote.websocket_connect(f'/api/ws/terminal?ticket={ticket}&server_id=71') as ws:
        assert ws.receive_text() == '{"type":"panellab-terminal-ready","version":1}'
        assert events == ['authenticated', 'shell', ('timeout', 10), ('write', 'cd -- /home/runner\r')]
        assert ws.receive_text() == 'Connected to trusted SSH server.\r\n'
