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
