"""Pooled SSH transports: reuse only for the same pinned target and credential.

No real network: socket and paramiko.Transport are replaced by fakes.
"""
import socket
import threading
import time
from types import SimpleNamespace

import paramiko
import pytest
from fastapi import HTTPException
from cryptography.hazmat.primitives.asymmetric import ed25519

from config import get_settings
from database import SessionLocal
from models.remote_connection import RemoteConnection
from models.server import Server
from models.user import User
from utils.cipher import encrypt_password
from tests.test_remote_connection import FakeTransport, pin, remote  # noqa: F401
from tests.test_private_key_auth import openssh, put_key


class FakeChannel:
    def __init__(self):
        self.closed = threading.Event()
    def close(self): self.closed.set()


class PoolTransport(FakeTransport):
    instances = []
    start_errors = []          # exceptions raised by start_client, consumed in order
    start_delay = 0.0
    def __init__(self, sock):
        super().__init__(sock)
        self.channels = []
        PoolTransport.instances.append(self)
    def start_client(self, timeout):
        self.events.append('handshake')
        if PoolTransport.start_delay: time.sleep(PoolTransport.start_delay)
        if PoolTransport.start_errors:
            raise PoolTransport.start_errors.pop(0)
    def is_active(self): return not self.closed
    def open_session(self, timeout=None, window_size=None, max_packet_size=None):
        if getattr(self, 'dead', False):  # TCP silently gone (e.g. after the host slept)
            raise EOFError()
        channel = FakeChannel()
        self.channels.append(channel)
        return channel


def logins():
    return [e for e in PoolTransport.events if isinstance(e, tuple) and e[0] in ('password', 'none', 'publickey')]


@pytest.fixture
def net(monkeypatch):
    from services import remote_connection as rc
    PoolTransport.events = FakeTransport.events = []
    PoolTransport.instances = []
    PoolTransport.start_errors = []
    PoolTransport.start_delay = 0.0
    FakeTransport.key = paramiko.RSAKey.generate(1024)
    monkeypatch.setattr(rc.socket, 'create_connection', lambda *a, **k: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(rc.paramiko, 'Transport', PoolTransport)
    sleeps = []
    monkeypatch.setattr(rc, '_sleep', sleeps.append, raising=False)
    reset = getattr(rc, 'reset_pool', lambda: None)
    reset()
    yield SimpleNamespace(rc=rc, sleeps=sleeps)
    reset()


def use(rc, body=None, **kwargs):
    with SessionLocal() as db:
        with rc.open_remote(db, db.get(User, 1), 71, **kwargs) as client:
            if body: return body(client)
            return client.get_transport().shared


def banner_eof():
    try:
        raise EOFError()
    except EOFError:
        try:
            raise paramiko.SSHException('Error reading SSH protocol banner')
        except paramiko.SSHException as exc:
            return exc


def test_requests_reuse_one_transport(remote, net):
    pin(remote)
    for _ in range(4):
        use(net.rc)
    assert len(logins()) == 1
    assert len(PoolTransport.instances) == 2  # the probe, then one pooled login
    assert not PoolTransport.instances[1].closed


def test_concurrent_requests_log_in_once(remote, net):
    pin(remote)
    PoolTransport.start_delay = 0.1
    errors = []
    def worker():
        try: use(net.rc)
        except Exception as exc: errors.append(exc)
    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads: t.start()
    for t in threads: t.join(5)
    assert errors == []
    assert len(logins()) == 1


def test_changed_credential_target_or_pin_logs_in_again(remote, net):
    pin(remote)
    first = use(net.rc)
    assert not first.closed and use(net.rc) is first
    with SessionLocal() as db:  # bypass the router so only the fingerprint can notice
        db.get(Server, 71).password_encrypted = encrypt_password(get_settings().secret_key, 'NEW_PASSWORD')
        db.commit()
    use(net.rc)
    assert logins()[-1] == ('password', 'runner', 'NEW_PASSWORD', False)
    assert len(logins()) == 2

    with SessionLocal() as db:  # target changed together with its pin
        db.get(Server, 71).port = 2200
        db.get(RemoteConnection, 71).port = 2200
        db.commit()
    use(net.rc)
    assert len(logins()) == 3

    FakeTransport.key = paramiko.RSAKey.generate(1024)
    with SessionLocal() as db:  # re-pinned host key
        row = db.get(RemoteConnection, 71)
        row.key_type, row.key_data = FakeTransport.key.get_name(), FakeTransport.key.get_base64()
        db.commit()
    use(net.rc)
    assert len(logins()) == 4

    with SessionLocal() as db:  # target changed without a new pin: refused before any reuse
        db.get(Server, 71).host = 'moved.invalid'
        db.commit()
    with pytest.raises(HTTPException) as exc:
        use(net.rc)
    assert exc.value.detail['code'] == 'target_changed'
    assert len(logins()) == 4
    # Every replaced transport has been closed.
    assert first.closed and all(t.closed for t in PoolTransport.instances)


def test_idle_and_max_age_eviction(remote, net, monkeypatch):
    pin(remote)
    now = [1000.0]
    monkeypatch.setattr(net.rc, '_clock', lambda: now[0])
    first = use(net.rc)
    now[0] += 119
    assert use(net.rc) is first
    now[0] += 121
    second = use(net.rc)
    assert second is not first and first.closed
    assert len(logins()) == 2
    # Used every 100 s, a transport is still replaced after 600 s.
    for _ in range(6):
        now[0] += 100
        last = use(net.rc)
    assert len(logins()) == 3 and second.closed and last is not second
    # The background sweep closes an idle transport without a new request.
    now[0] += 121
    net.rc.sweep()
    assert last.closed


def test_banner_eof_is_retried_once(remote, net):
    pin(remote)
    PoolTransport.start_errors = [banner_eof()]
    use(net.rc)
    assert net.sleeps == [1.5]
    assert len(logins()) == 1


def test_second_refusal_reports_chinese_message(remote, net):
    pin(remote)
    PoolTransport.start_errors = [banner_eof(), EOFError()]
    result = remote.post('/api/servers/71/connection/check')
    assert result.status_code == 503
    assert result.json()['detail'] == {
        'code': 'connection_refused_by_server',
        'message': '服务器暂时拒绝了新的 SSH 连接（可能连接过于频繁），请稍后重试'}
    assert net.sleeps == [1.5]
    assert 'banner' not in result.text.lower()
    assert len(PoolTransport.instances) == 3  # probe + two attempts


@pytest.mark.parametrize('where, error, code, message', [
    ('socket', ConnectionRefusedError(), 'connection_failed', '无法连接服务器的 SSH 端口'),
    ('socket', socket.timeout('PRIVATE'), 'connection_failed', '连接服务器超时'),
    ('socket', socket.gaierror(8, 'PRIVATE'), 'connection_failed', '无法解析服务器地址'),
    ('start', ConnectionResetError(), 'connection_refused_by_server',
     '服务器暂时拒绝了新的 SSH 连接（可能连接过于频繁），请稍后重试'),
    ('start', paramiko.SSHException('PRIVATE negotiation'), 'connection_failed', 'SSH 连接失败'),
    ('auth', paramiko.AuthenticationException('PRIVATE'), 'authentication_failed', 'SSH 认证失败'),
    ('auth', RuntimeError('PRIVATE_PASSWORD'), 'connection_failed', 'SSH 连接失败'),
    ('start', paramiko.SSHException('Negotiation timed out.'), 'connection_failed', '连接服务器超时'),
    ('auth', paramiko.AuthenticationException('Authentication timeout.'), 'connection_failed', '连接服务器超时'),
    ('kex', None, 'connection_failed', '连接服务器超时'),
])
def test_connect_phase_errors_are_specific(remote, net, monkeypatch, where, error, code, message, caplog):
    pin(remote)
    def throw(*a, **k): raise error
    if where == 'socket':
        monkeypatch.setattr(net.rc.socket, 'create_connection', throw)
    elif where == 'start':
        PoolTransport.start_errors = [error, error]
    elif where == 'kex':  # paramiko 5 returns from start_client on timeout without raising
        monkeypatch.setattr(PoolTransport, 'initial_kex_done', False, raising=False)
    else:
        monkeypatch.setattr(PoolTransport, 'auth_password', throw)
    result = remote.post('/api/servers/71/connection/check')
    assert result.status_code == 503
    assert result.json()['detail'] == {'code': code, 'message': message}
    assert 'PRIVATE' not in result.text and 'PRIVATE' not in caplog.text
    assert net.sleeps == ([1.5] if code == 'connection_refused_by_server' else [])


@pytest.mark.parametrize('error', [paramiko.SSHException('PRIVATE'), OSError('PRIVATE'), EOFError()])
def test_errors_inside_the_body_are_not_connection_failures(remote, net, error):
    pin(remote)
    def body(client): raise error
    with pytest.raises(HTTPException) as exc:
        use(net.rc, body)
    assert exc.value.detail['code'] != 'connection_failed'
    assert exc.value.detail['code'] == 'remote_connection_lost'
    assert 'PRIVATE' not in str(exc.value.detail)
    # An HTTP error raised by the caller passes through unchanged.
    def refuse(client): net.rc.fail('path_unavailable', 'x', 404)
    with pytest.raises(HTTPException) as exc:
        use(net.rc, refuse)
    assert exc.value.status_code == 404 and exc.value.detail['code'] == 'path_unavailable'


def test_handshake_lets_body_errors_propagate(remote, net):
    with SessionLocal() as db:
        row = db.get(Server, 71)
        with pytest.raises(OSError):
            with net.rc.handshake(row):
                raise OSError('in use')


def test_timeout_does_not_cut_off_another_request(remote, net, monkeypatch):
    pin(remote)
    monkeypatch.setattr(net.rc, 'DEADLINE_GRACE', 5)
    b_ready, b_release, b_done = threading.Event(), threading.Event(), threading.Event()
    seen = {}
    def request_b():
        def body(client):
            seen['b'] = client.get_transport().open_session(timeout=1)
            b_ready.set()
            b_release.wait(5)
            seen['b_closed_early'] = seen['b'].closed.is_set()
            return client.get_transport().shared
        seen['b_transport'] = use(net.rc, body)
        b_done.set()
    thread = threading.Thread(target=request_b)
    thread.start()
    assert b_ready.wait(5)
    def body_a(client):
        with net.rc.remote_deadline(client, timeout=0.05):
            channel = client.get_transport().open_session(timeout=1)
            seen['a'] = channel
            assert channel.closed.wait(2)  # closing our channel wakes the wait
            raise EOFError()
    with pytest.raises(HTTPException) as exc:
        use(net.rc, body_a)
    assert exc.value.detail['code'] == 'remote_timeout'
    shared = PoolTransport.instances[1]
    assert seen['a'].closed.is_set() and not seen['b'].closed.is_set()
    assert not shared.closed  # B is still running on it
    # The timed-out transport left the pool: the next request logs in again.
    fresh = use(net.rc)
    assert fresh is not shared and len(logins()) == 2
    b_release.set()
    assert b_done.wait(5)
    assert seen['b_closed_early'] is False and seen['b_transport'] is shared
    assert shared.closed  # closed once its last user finished


def test_wedged_wait_falls_back_to_closing_the_dropped_transport(remote, net, monkeypatch):
    pin(remote)
    monkeypatch.setattr(net.rc, 'DEADLINE_GRACE', 0.05)
    def body(client):
        transport = PoolTransport.instances[-1]
        with net.rc.remote_deadline(client, timeout=0.02):
            deadline = time.monotonic() + 2
            while not transport.closed and time.monotonic() < deadline:
                time.sleep(0.01)  # ignores channel closure, like an unbounded event wait
        return transport
    with pytest.raises(HTTPException) as exc:
        use(net.rc, body)
    assert exc.value.detail['code'] == 'remote_timeout'
    assert PoolTransport.instances[1].closed
    use(net.rc)
    assert len(logins()) == 2


def test_dedicated_connection_is_not_pooled(remote, net):
    pin(remote)
    pooled = use(net.rc)
    dedicated = use(net.rc, dedicated=True)
    assert dedicated is not pooled and dedicated.closed and not pooled.closed
    assert use(net.rc) is pooled


def test_edit_key_save_trust_and_delete_invalidate(remote, net):
    pin(remote)
    first = use(net.rc)
    assert not first.closed
    body = {'name': 'fixture', 'host': 'fixture.invalid', 'port': 2222, 'username': 'runner'}
    assert remote.put('/api/servers/71', json=body).status_code == 200
    assert first.closed

    second = use(net.rc)
    assert not second.closed
    assert put_key(remote, openssh(ed25519.Ed25519PrivateKey.generate())).status_code == 200
    assert second.closed

    third = use(net.rc)
    assert not third.closed
    pin(remote)
    assert third.closed

    fourth = use(net.rc)
    assert not fourth.closed
    assert remote.delete('/api/servers/71/private-key').status_code == 200
    assert fourth.closed

    fifth = use(net.rc)
    assert not fifth.closed
    assert remote.delete('/api/servers/71').status_code == 200
    assert fifth.closed


def test_requests_per_transport_are_bounded(remote, net, monkeypatch):
    """sshd caps sessions per connection (MaxSessions); extra requests wait for a slot."""
    pin(remote)
    monkeypatch.setattr(net.rc, 'MAX_REQUESTS_PER_TRANSPORT', 1)
    monkeypatch.setattr(net.rc, 'REQUEST_SLOT_TIMEOUT', 5)
    holding, release_a, b_done = threading.Event(), threading.Event(), threading.Event()
    def body_a(client):
        holding.set()
        release_a.wait(5)
    a = threading.Thread(target=lambda: use(net.rc, body_a))
    a.start()
    assert holding.wait(5)
    b = threading.Thread(target=lambda: (use(net.rc), b_done.set()))
    b.start()
    assert not b_done.wait(0.2)
    release_a.set()
    assert b_done.wait(5)
    a.join(5); b.join(5)
    assert len(logins()) == 1

    monkeypatch.setattr(net.rc, 'REQUEST_SLOT_TIMEOUT', 0.05)
    holding.clear(); release_a.clear()
    a = threading.Thread(target=lambda: use(net.rc, body_a))
    a.start()
    assert holding.wait(5)
    with pytest.raises(HTTPException) as exc:
        use(net.rc)
    release_a.set()
    a.join(5)
    assert exc.value.detail == {'code': 'remote_busy', 'message': '服务器上的 SSH 请求过多，请稍后重试'}


@pytest.mark.parametrize('error, dropped', [
    (paramiko.SSHException('Timeout opening channel.'), True),
    (paramiko.ChannelException(1, 'Administratively prohibited'), False),
])
def test_failed_channel_open_drops_only_a_broken_transport(remote, net, monkeypatch, error, dropped):
    pin(remote)
    first = use(net.rc)
    def refuse(self, **kwargs): raise error
    monkeypatch.setattr(PoolTransport, 'open_session', refuse)
    with pytest.raises(HTTPException) as exc:
        use(net.rc, lambda client: client.get_transport().open_session(timeout=1))
    assert exc.value.detail['code'] == 'remote_connection_lost'
    assert first.closed is dropped
    assert (use(net.rc) is first) is not dropped


@pytest.mark.parametrize('failure, attempts, code', [
    ('refused', 2, 'connection_refused_by_server'),
    ('port', 1, 'connection_failed'),
])
def test_waiters_share_a_failed_login(remote, net, monkeypatch, failure, attempts, code):
    pin(remote)
    probe_count = len(PoolTransport.instances)
    tries = []
    real_connect = net.rc.socket.create_connection
    def connect(*args, **kwargs):
        tries.append(1)
        time.sleep(0.2)  # long enough for every caller to queue behind the first login
        if failure == 'port': raise ConnectionRefusedError()
        return real_connect(*args, **kwargs)
    monkeypatch.setattr(net.rc.socket, 'create_connection', connect)
    if failure == 'refused':
        PoolTransport.start_errors = [banner_eof(), EOFError()]
    codes = []
    def worker():
        try: use(net.rc)
        except HTTPException as exc: codes.append(exc.detail['code'])
    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads: t.start()
    for t in threads: t.join(10)
    assert codes == [code] * 6
    assert len(tries) == attempts
    assert net.sleeps == ([1.5] if failure == 'refused' else [])
    assert len(PoolTransport.instances) - probe_count == (2 if failure == 'refused' else 0)
    # Once the failure is published, a new caller starts a fresh attempt.
    monkeypatch.setattr(net.rc.socket, 'create_connection', real_connect)
    use(net.rc)
    assert len(logins()) == 1


def test_slot_waiter_does_not_run_on_a_retired_transport(remote, net, monkeypatch):
    pin(remote)
    monkeypatch.setattr(net.rc, 'MAX_REQUESTS_PER_TRANSPORT', 1)
    holding, release_a = threading.Event(), threading.Event()
    seen = {}
    def body_a(client):
        holding.set()
        release_a.wait(5)
    a = threading.Thread(target=lambda: seen.update(a=use(net.rc, lambda c: (body_a(c), c.get_transport().shared)[1])))
    a.start()
    assert holding.wait(5)
    b = threading.Thread(target=lambda: seen.update(b=use(net.rc)))
    b.start()
    time.sleep(0.2)  # B now waits for A's slot
    net.rc.invalidate(71)
    release_a.set()
    a.join(5); b.join(5)
    assert seen['b'] is not seen['a']
    assert seen['a'].closed and not seen['b'].closed
    assert len(logins()) == 2


def test_a_long_request_does_not_make_its_transport_idle(remote, net, monkeypatch):
    pin(remote)
    now = [1000.0]
    monkeypatch.setattr(net.rc, '_clock', lambda: now[0])
    holding, release_a = threading.Event(), threading.Event()
    seen = {}
    def body_a(client):
        holding.set()
        release_a.wait(5)
        return client.get_transport().shared
    a = threading.Thread(target=lambda: seen.update(a=use(net.rc, body_a)))
    a.start()
    assert holding.wait(5)
    now[0] += 200  # A has been busy for longer than the idle limit
    seen['b'] = use(net.rc)
    release_a.set()
    a.join(5)
    assert seen['b'] is seen['a'] and len(logins()) == 1


class AgentTransport(PoolTransport):
    """Exec channels answered in order from AgentTransport.replies (shared across logins)."""
    replies = []
    def open_session(self, timeout=None, window_size=None, max_packet_size=None):
        from tests.test_agent_client import Channel
        status, stdout = AgentTransport.replies.pop(0)
        return Channel(stdout=stdout, status=status)


def agent_call(rc):
    from services import agent_client
    with SessionLocal() as db:
        return agent_client.call(db, db.get(User, 1), 71, 'status')


@pytest.mark.parametrize('replies, outcome, login_count', [
    ([(4, b''), (0, b'{"ok":true,"result":{"v":1}}\n')], {'v': 1}, 2),
    ([(4, b''), (4, b'')], 'agent_permission', 2),
    ([(3, b'')], 'agent_unreachable', 1),
    ([(127, b'')], 'agent_not_installed', 1),
])
def test_agent_permission_is_retried_once_on_a_fresh_login(remote, net, monkeypatch, replies, outcome, login_count):
    """A pooled login predates install.sh adding the user to group panellab; a new login sees it."""
    monkeypatch.setattr(net.rc.paramiko, 'Transport', AgentTransport)
    pin(remote)
    AgentTransport.replies = list(replies)
    if isinstance(outcome, dict):
        assert agent_call(net.rc) == outcome
    else:
        with pytest.raises(HTTPException) as exc:
            agent_call(net.rc)
        assert exc.value.detail['code'] == outcome
    assert len(logins()) == login_count
    assert AgentTransport.replies == []
    if login_count == 2:  # the stale transport is gone; the fresh one stays pooled
        assert PoolTransport.instances[1].closed and not PoolTransport.instances[2].closed
    else:  # other failures do not throw away the pooled login
        assert not PoolTransport.instances[1].closed


def test_invalidate_forces_a_new_login_while_the_old_transport_is_in_use(remote, net):
    pin(remote)
    def body(client):
        net.rc.invalidate(71)
        inner = use(net.rc)
        assert not client.get_transport().shared.closed  # still usable by this request
        return client.get_transport().shared, inner
    old, fresh = use(net.rc, body)
    assert fresh is not old and old.closed and not fresh.closed
    assert len(logins()) == 2


def test_wall_clock_gap_evicts_even_when_monotonic_says_fresh(remote, net, monkeypatch):
    """macOS monotonic time stops while the machine sleeps; wall time does not."""
    pin(remote)
    mono, wall = [1000.0], [5000.0]
    monkeypatch.setattr(net.rc, '_clock', lambda: mono[0])
    monkeypatch.setattr(net.rc, '_wall', lambda: wall[0], raising=False)
    first = use(net.rc)
    mono[0] += 1
    wall[0] += 3600  # slept for an hour
    second = use(net.rc)
    assert second is not first and first.closed and len(logins()) == 2
    wall[0] -= 30  # the wall clock jumped backwards: do not trust the entry
    third = use(net.rc)
    assert third is not second and second.closed and len(logins()) == 3
    wall[0] += 3600
    net.rc.sweep()
    assert third.closed


def test_dead_reused_transport_is_replaced_before_anything_runs(remote, net):
    pin(remote)
    first = use(net.rc)
    first.dead = True
    runs = []
    def body(client):
        runs.append(1)
        channel = client.get_transport().open_session(timeout=1)
        return client.get_transport().shared, channel
    shared, channel = use(net.rc, body)
    assert runs == [1] and len(logins()) == 2
    assert shared is not first and channel in shared.channels
    assert first.closed and not shared.closed


def test_dead_fresh_transport_is_not_retried(remote, net, monkeypatch):
    pin(remote)
    monkeypatch.setattr(PoolTransport, 'dead', True, raising=False)
    with pytest.raises(HTTPException) as exc:
        use(net.rc, lambda client: client.get_transport().open_session(timeout=1))
    assert exc.value.detail['code'] == 'remote_connection_lost'
    assert len(logins()) == 1


def test_no_retry_once_a_channel_was_opened(remote, net):
    pin(remote)
    first = use(net.rc)
    def body(client):
        client.get_transport().open_session(timeout=1)  # the operation may now run remotely
        first.dead = True
        client.get_transport().open_session(timeout=1)
    with pytest.raises(HTTPException) as exc:
        use(net.rc, body)
    assert exc.value.detail['code'] == 'remote_connection_lost'
    assert len(logins()) == 1


def test_app_shutdown_closes_pooled_transports(remote, net):
    from fastapi.testclient import TestClient
    from main import app
    pin(remote)
    pooled = use(net.rc)
    with TestClient(app):
        pass
    assert pooled.closed
