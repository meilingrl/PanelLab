"""Pinned SSH transports, pooled per server. No credentials, agents or local keys before pin check."""
import base64
import errno
import hashlib
import logging
import socket
import time
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

import jwt
import paramiko
from fastapi import HTTPException

from config import get_settings
from models.remote_connection import RemoteConnection
from models.server import Server
from utils.cipher import decrypt_password

CONNECT_TIMEOUT = 10
IDLE_SECONDS = 120
MAX_AGE_SECONDS = 600
RETRY_DELAY = 1.5
DEADLINE_GRACE = 5
SWEEP_INTERVAL = 30
# OpenSSH allows 10 sessions per connection (MaxSessions); leave headroom for a request
# that briefly holds two channels.
MAX_REQUESTS_PER_TRANSPORT = 6
REQUEST_SLOT_TIMEOUT = 30
_clock = time.monotonic
_sleep = time.sleep
log = logging.getLogger(__name__)


def fail(code, message, status=409):
    raise HTTPException(status_code=status, detail={'code': code, 'message': message})


def observed_at():
    return datetime.now(timezone.utc).isoformat()


def owned_server(db, user, server_id):
    row = db.query(Server).filter(Server.id == server_id, Server.user_id == user.id).first()
    if row is None:
        fail('server_not_found', 'Server not found', 404)
    return row


def target(row):
    return {'host': row.host, 'port': row.port, 'username': row.username}


def fingerprint(key):
    return 'SHA256:' + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')


# ------------------------------------------------------------------ connect phase

REFUSED = ('connection_refused_by_server', '服务器暂时拒绝了新的 SSH 连接（可能连接过于频繁），请稍后重试')
TIMED_OUT = ('connection_failed', '连接服务器超时')
UNREACHABLE_ERRNOS = {errno.EHOSTUNREACH, errno.ENETUNREACH}


def _connect_error(exc):
    """(code, message) for an error raised while connecting or logging in.

    Only the exception type and a fixed paramiko banner marker are inspected; the
    exception text never reaches the client or the log.
    """
    if isinstance(exc, paramiko.AuthenticationException):
        if str(exc) == 'Authentication timeout.':  # paramiko's fixed auth-wait text
            return TIMED_OUT
        return 'authentication_failed', 'SSH 认证失败'
    if isinstance(exc, socket.gaierror):
        return 'connection_failed', '无法解析服务器地址'
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return TIMED_OUT
    if isinstance(exc, ConnectionRefusedError) or getattr(exc, 'errno', None) in UNREACHABLE_ERRNOS:
        return 'connection_failed', '无法连接服务器的 SSH 端口'
    if isinstance(exc, (EOFError, ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
        return REFUSED
    if isinstance(exc, paramiko.SSHException) and str(exc) == 'Negotiation timed out.':  # older paramiko kex timeout
        return TIMED_OUT
    if isinstance(exc, paramiko.SSHException) and str(exc).startswith('Error reading SSH protocol banner'):
        # sshd throttling (MaxStartups, PerSourcePenalties) closes before its banner.
        cause = exc.__cause__ or exc.__context__
        return TIMED_OUT if isinstance(cause, (socket.timeout, TimeoutError)) else REFUSED
    return 'connection_failed', 'SSH 连接失败'


@contextmanager
def _connect_phase():
    try:
        yield
    except HTTPException:
        raise
    except Exception as exc:
        code, message = _connect_error(exc)
        log.warning('SSH connect failed: %s (%s)', code, type(exc).__name__)
        raise HTTPException(status_code=503, detail={'code': code, 'message': message}) from None


def _close(transport):
    try:
        transport.close()
    except Exception:
        pass


def _start(row):
    """Open TCP and finish key exchange. Nothing is authenticated yet."""
    sock = transport = None
    try:
        with _connect_phase():
            sock = socket.create_connection((row.host, row.port), timeout=CONNECT_TIMEOUT)
            transport = paramiko.Transport(sock)
            transport.banner_timeout = CONNECT_TIMEOUT
            transport.auth_timeout = CONNECT_TIMEOUT
            transport.channel_timeout = CONNECT_TIMEOUT
            transport.start_client(timeout=CONNECT_TIMEOUT)
            if not getattr(transport, 'initial_kex_done', True):
                # paramiko 5 returns from start_client on timeout without raising.
                raise socket.timeout()
        return transport
    except BaseException:
        if transport is not None:
            _close(transport)
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
        raise


def _with_retry(attempt):
    """Retry once, after RETRY_DELAY, when the server refused a new connection.

    An EOF during authentication counts as a refusal, so credentials may be sent once more.
    """
    try:
        return attempt()
    except HTTPException as exc:
        if not (isinstance(exc.detail, dict) and exc.detail.get('code') == REFUSED[0]):
            raise
    _sleep(RETRY_DELAY)
    return attempt()


@contextmanager
def handshake(row):
    """Unauthenticated transport for reading the host key.

    Only connect-phase errors are mapped; errors raised in the body propagate unchanged.
    """
    transport = _with_retry(lambda: _start(row))
    try:
        yield transport
    finally:
        _close(transport)


def probe(db, user, server_id, auth_mode='password'):
    row = owned_server(db, user, server_id)
    with handshake(row) as transport:
        key = transport.get_remote_server_key()
    expires = int(time.time()) + 300
    payload = {'purpose': 'remote-trust', 'owner': user.id, 'server': row.id,
               **target(row), 'key_type': key.get_name(), 'key_data': key.get_base64(),
               'fingerprint': fingerprint(key), 'auth_mode': auth_mode, 'exp': expires}
    token = jwt.encode(payload, get_settings().secret_key, algorithm='HS256')
    return {'host': row.host, 'port': row.port, 'key_type': payload['key_type'],
            'fingerprint': payload['fingerprint'], 'token': token, 'expires_at': expires}


def trust(db, user, server_id, token):
    row = owned_server(db, user, server_id)
    try:
        p = jwt.decode(token, get_settings().secret_key, algorithms=['HS256'], options={'require': ['exp']})
        if not (p['purpose'] == 'remote-trust' and p['owner'] == user.id and p['server'] == row.id
                and all(p[k] == v for k, v in target(row).items())
                and p['auth_mode'] in ('password', 'key', 'tailscale')
                and p['key_type'] and p['key_data'] and p['fingerprint']):
            raise ValueError('Invalid trust binding')
    except Exception:
        fail('invalid_trust_token', 'Trust confirmation expired or target changed; probe again')
    pin = db.get(RemoteConnection, row.id)
    if pin is None:
        pin = RemoteConnection(server_id=row.id)
        db.add(pin)
    for k in ('host', 'port', 'username', 'key_type', 'key_data', 'fingerprint', 'auth_mode'):
        setattr(pin, k, p[k])
    pin.owner_id = user.id
    db.commit()
    invalidate(row.id)
    return {'fingerprint': pin.fingerprint, 'auth_mode': pin.auth_mode}


def require_pin(db, user, server_id):
    row = owned_server(db, user, server_id)
    pin = db.get(RemoteConnection, row.id)
    if pin is None or pin.owner_id != user.id:
        fail('trust_required', 'Verify and trust the SSH host fingerprint first')
    if any(getattr(pin, k) != v for k, v in target(row).items()):
        fail('target_changed', 'Server target changed; verify and trust it again')
    return row, pin


def connection_summary(db, user, server_id):
    row = owned_server(db, user, server_id)
    pin = db.get(RemoteConnection, row.id)
    valid = bool(pin and pin.owner_id == user.id and all(getattr(pin, k) == v for k, v in target(row).items()))
    return {'configured': bool(pin), 'pinned': valid, **target(row),
            'fingerprint': pin.fingerprint if pin else None,
            'key_type': pin.key_type if pin else None, 'auth_mode': pin.auth_mode if pin else None}


def _login(db, row, pin):
    """Connect, check the pinned host key, then authenticate. Returns an authenticated transport."""
    def attempt():
        transport = _start(row)
        try:
            with _connect_phase():
                key = transport.get_remote_server_key()
                if key.get_name() != pin.key_type or key.get_base64() != pin.key_data:
                    fail('host_key_changed', 'SSH host key changed; verify and trust it again')
                if pin.auth_mode == 'password':
                    password = decrypt_password(get_settings().secret_key, row.password_encrypted) if row.password_encrypted else None
                    if not password:
                        fail('authentication_failed', '请先为该服务器配置有效的 SSH 密码', 503)
                    transport.auth_password(row.username, password, fallback=False)
                elif pin.auth_mode == 'key':
                    from services.remote_keys import load
                    transport.auth_publickey(row.username, load(db, row))
                elif pin.auth_mode == 'tailscale':
                    transport.auth_none(row.username)
                else:
                    fail('authentication_failed', '不支持的 SSH 认证方式', 503)
                if not transport.is_authenticated():
                    fail('authentication_failed', 'SSH 认证失败', 503)
            return transport
        except BaseException:
            _close(transport)
            raise
    return _with_retry(attempt)


# ------------------------------------------------------------------ connection pool
#
# One authenticated transport per (owner_id, server_id), shared by concurrent requests.
# Each request opens its own channels and closes them; the transport is closed only
# when it has left the pool and its last user has finished.

class _Entry:
    __slots__ = ('transport', 'fingerprint', 'created', 'last_used', 'users', 'retired', 'slots')

    def __init__(self, transport, fingerprint, now):
        self.transport, self.fingerprint = transport, fingerprint
        self.created = self.last_used = now
        self.users, self.retired = 1, False
        self.slots = threading.BoundedSemaphore(MAX_REQUESTS_PER_TRANSPORT)


_pool = {}
_pool_lock = threading.Lock()
_login_locks = {}
_logins_in_flight = {}
_sweeper = None
LOGIN_WAIT_TIMEOUT = 120  # longer than a login plus its retry (2 x 3 x CONNECT_TIMEOUT + RETRY_DELAY)


class _LoginAttempt:
    """One pooled login for a key. Callers queued behind it share its outcome."""
    __slots__ = ('fingerprint', 'done', 'error')

    def __init__(self, fingerprint):
        self.fingerprint, self.done, self.error = fingerprint, threading.Event(), None


def _credential_fingerprint(db, row, pin):
    """Everything a pooled login depended on. Secrets enter only as a hash of their ciphertext."""
    from models.server_key import ServerPrivateKey
    stored = db.get(ServerPrivateKey, row.id)
    digest = hashlib.sha256()
    for part in (row.password_encrypted or '', stored.key_encrypted if stored else '',
                 stored.key_type if stored else '', str(stored.owner_id) if stored else ''):
        digest.update(part.encode('utf-8') + b'\0')
    return (row.host, row.port, row.username, pin.key_type, pin.key_data, pin.auth_mode, digest.hexdigest())


def _retire_locked(key, entry):
    """Take entry out of the pool. Returns its transport if nobody is using it any more."""
    if _pool.get(key) is entry:
        del _pool[key]
    entry.retired = True
    return entry.transport if entry.users == 0 else None


def _close_all(transports):
    for transport in transports:
        if transport is not None:
            _close(transport)


def _acquire(key, fingerprint):
    stale = None
    with _pool_lock:
        entry = _pool.get(key)
        if entry is not None:
            now = _clock()
            # A transport busy with a long request is not idle, whatever last_used says.
            idle = entry.users == 0 and now - entry.last_used >= IDLE_SECONDS
            if (entry.fingerprint == fingerprint and entry.transport.is_active()
                    and now - entry.created < MAX_AGE_SECONDS and not idle):
                entry.users += 1
                entry.last_used = now
                return entry
            stale = _retire_locked(key, entry)
    _close_all([stale])
    return None


def _put(key, fingerprint, transport):
    global _sweeper
    stale = None
    with _pool_lock:
        old = _pool.get(key)
        if old is not None:
            stale = _retire_locked(key, old)
        entry = _pool[key] = _Entry(transport, fingerprint, _clock())
        if _sweeper is None:
            _sweeper = threading.Thread(target=_sweep_loop, name='ssh-pool-sweeper', daemon=True)
            _sweeper.start()
    _close_all([stale])
    return entry


def _release(key, entry, broken=False):
    closing = None
    with _pool_lock:
        entry.users -= 1
        entry.last_used = _clock()
        if broken or not entry.transport.is_active():
            _retire_locked(key, entry)
        if entry.retired and entry.users == 0:
            closing = entry.transport
    _close_all([closing])


def _retire(key, entry):
    """Stop handing this transport out; it closes when its current users are done."""
    with _pool_lock:
        closing = _retire_locked(key, entry)
    _close_all([closing])


def _discard(key):
    with _pool_lock:
        entry = _pool.get(key)
        closing = _retire_locked(key, entry) if entry is not None else None
    _close_all([closing])


def invalidate(server_id):
    """Drop pooled transports for a server after its target, pin, credential or existence changed."""
    with _pool_lock:
        closing = [_retire_locked(key, entry) for key, entry in list(_pool.items()) if key[1] == server_id]
    _close_all(closing)


def sweep():
    """Close transports idle for IDLE_SECONDS or older than MAX_AGE_SECONDS."""
    now = _clock()
    with _pool_lock:
        closing = [_retire_locked(key, entry) for key, entry in list(_pool.items())
                   if now - entry.created >= MAX_AGE_SECONDS or not entry.transport.is_active()
                   or (entry.users == 0 and now - entry.last_used >= IDLE_SECONDS)]
    _close_all(closing)


def _sweep_loop():
    global _sweeper
    while True:
        time.sleep(SWEEP_INTERVAL)
        try:
            sweep()
        except Exception as exc:  # never let the sweeper die silently with a leaked pool
            log.warning('SSH pool sweep failed: %s', type(exc).__name__)
        with _pool_lock:
            if not _pool:
                _sweeper = None
                return


def reset_pool():
    """Close every pooled transport, including ones in use (tests and shutdown)."""
    with _pool_lock:
        entries = list(_pool.values())
        _pool.clear()
        for entry in entries:
            entry.retired = True
    _close_all([entry.transport for entry in entries])


def _login_lock(key):
    with _pool_lock:
        return _login_locks.setdefault(key, threading.Lock())


class _RequestTransport:
    """The shared transport as one request sees it: every channel it opens is recorded."""

    def __init__(self, shared, request):
        self.shared = shared
        self._request = request

    def __getattr__(self, name):
        return getattr(self.shared, name)

    def _open(self, opener, args, kwargs):
        try:
            channel = opener(*args, **kwargs)
        except paramiko.ChannelException:
            raise  # the server refused this channel; the transport itself is fine
        except (paramiko.SSHException, EOFError, OSError):
            self._request.broken = True  # e.g. "Timeout opening channel": do not reuse
            raise
        return self._request._track(channel)

    def open_session(self, *args, **kwargs):
        return self._open(self.shared.open_session, args, kwargs)

    def open_channel(self, *args, **kwargs):
        return self._open(self.shared.open_channel, args, kwargs)

    def open_sftp_client(self):
        return paramiko.SFTPClient.from_transport(self)


class _RequestClient(paramiko.SSHClient):
    """SSHClient API over a shared transport; closing it closes only this request's channels."""

    def __init__(self, transport, on_abort=None):
        super().__init__()
        # Authentication already happened explicitly; SSHClient only supplies SFTP/shell APIs.
        self._transport = _RequestTransport(transport, self)
        self.shared = transport
        self._channels = []
        self._channels_lock = threading.Lock()
        self._aborted = False
        self._on_abort = on_abort
        self.broken = False

    def _track(self, channel):
        with self._channels_lock:
            aborted = self._aborted
            if not aborted:
                self._channels.append(channel)
        if aborted:
            channel.close()
            fail('remote_timeout', 'Remote operation timed out', 503)
        return channel

    def _close_channels(self):
        with self._channels_lock:
            channels, self._channels = self._channels, []
        for channel in channels:
            try:
                channel.close()
            except Exception:
                pass

    def abort(self):
        """Deadline expired: leave the pool first, then close this request's channels."""
        with self._channels_lock:
            self._aborted = True
        if self._on_abort is not None:
            self._on_abort()
        self._close_channels()

    def close(self):
        self._close_channels()


def _login_shared(db, key, row, pin, fingerprint):
    """Pooled entry (users already counted) from at most one login in flight per key.

    Callers that queued behind a login get its result: the pooled transport or the same
    error, without connecting themselves. A caller arriving after the outcome was
    published starts a fresh attempt.
    """
    while True:
        entry = _acquire(key, fingerprint)
        if entry is not None:
            return entry
        with _pool_lock:
            attempt = _logins_in_flight.get(key)
            leader = attempt is None
            if leader:
                attempt = _logins_in_flight[key] = _LoginAttempt(fingerprint)
        if leader:
            try:
                with _login_lock(key):  # also serialises against dedicated logins
                    return _put(key, fingerprint, _login(db, row, pin))
            except HTTPException as exc:
                attempt.error = (exc.status_code, exc.detail)
                raise
            except BaseException:
                attempt.error = (503, {'code': 'connection_failed', 'message': 'SSH 连接失败'})
                raise
            finally:
                with _pool_lock:
                    if _logins_in_flight.get(key) is attempt:
                        del _logins_in_flight[key]
                attempt.done.set()
        if not attempt.done.wait(LOGIN_WAIT_TIMEOUT):
            fail(*TIMED_OUT, 503)
        if attempt.error is not None and attempt.fingerprint == fingerprint:
            status, detail = attempt.error
            raise HTTPException(status_code=status, detail=dict(detail) if isinstance(detail, dict) else detail)
        # Success (reuse it on the next pass) or a login for other credentials: go round again.


def _pooled_entry(db, key, row, pin, fingerprint):
    """A pooled entry plus one of its request slots, never on a retired transport."""
    while True:
        entry = _login_shared(db, key, row, pin, fingerprint)
        if not entry.slots.acquire(timeout=REQUEST_SLOT_TIMEOUT):
            _release(key, entry)
            fail('remote_busy', '服务器上的 SSH 请求过多，请稍后重试', 503)
        if not entry.retired:
            return entry
        # Retired (invalidated, timed out, aged) while we waited for the slot.
        entry.slots.release()
        _release(key, entry)


@contextmanager
def open_remote(db, user, server_id, *, dedicated=False):
    """Authenticated client for one request.

    Pooled by default. dedicated=True gives a private connection closed on exit
    (the terminal's long-lived PTY). The pin is always checked first.
    """
    key = (user.id, server_id)
    try:
        row, pin = require_pin(db, user, server_id)
    except HTTPException:
        _discard(key)
        raise
    entry = None
    if dedicated:
        with _login_lock(key):
            transport = _login(db, row, pin)
    else:
        entry = _pooled_entry(db, key, row, pin, _credential_fingerprint(db, row, pin))
        transport = entry.transport
    pooled = entry
    client = _RequestClient(transport, (lambda: _retire(key, pooled)) if pooled else None)
    broken = False
    try:
        yield client
    except HTTPException:
        raise
    except (paramiko.SSHException, EOFError, OSError) as exc:
        # In use, not connecting: never reported as connection_failed.
        broken = (not isinstance(exc, paramiko.ChannelException)
                  and isinstance(exc, (paramiko.SSHException, EOFError, socket.timeout, ConnectionError)))
        log.warning('SSH request failed: %s', type(exc).__name__)
        raise HTTPException(status_code=503, detail={
            'code': 'remote_connection_lost', 'message': '与服务器的 SSH 连接中断'}) from None
    finally:
        client.close()
        if entry is None:
            _close(transport)
        else:
            entry.slots.release()
            _release(key, entry, broken or client.broken)


@contextmanager
def remote_deadline(client, timeout=CONNECT_TIMEOUT):
    """Bound one request's remote work.

    On a pooled client only this request's channels are closed and the transport leaves
    the pool, so other requests sharing it keep running. If the request is still stuck
    DEADLINE_GRACE seconds later (a Paramiko wait that ignores channel closure), the
    transport itself is closed; it is already out of the pool.
    """
    expired = threading.Event()
    done = threading.Event()
    request = client if isinstance(client, _RequestClient) else None
    transport = client.get_transport()

    def abort():
        expired.set()
        if request is None:
            transport.close()
            return
        request.abort()
        if not done.wait(DEADLINE_GRACE):
            _close(request.shared)

    timer = threading.Timer(timeout, abort)
    timer.daemon = True
    timer.start()
    try:
        yield
    finally:
        done.set()
        timer.cancel()
        if expired.is_set():
            fail('remote_timeout', 'Remote operation timed out', 503)
