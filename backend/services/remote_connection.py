"""Pinned SSH transport. No credentials, agents or local keys before pin check."""
import base64
import hashlib
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


@contextmanager
def handshake(row):
    sock = transport = None
    try:
        sock = socket.create_connection((row.host, row.port), timeout=CONNECT_TIMEOUT)
        transport = paramiko.Transport(sock)
        transport.banner_timeout = CONNECT_TIMEOUT
        transport.auth_timeout = CONNECT_TIMEOUT
        transport.channel_timeout = CONNECT_TIMEOUT
        transport.start_client(timeout=CONNECT_TIMEOUT)
        yield transport
    except HTTPException:
        raise
    except Exception:
        fail('connection_failed', 'SSH connection or authentication failed', 503)
    finally:
        if transport is not None:
            transport.close()
        if sock is not None:
            sock.close()


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
                and p['auth_mode'] in ('password', 'tailscale')
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


@contextmanager
def open_remote(db, user, server_id):
    row, pin = require_pin(db, user, server_id)
    with handshake(row) as transport:
        key = transport.get_remote_server_key()
        if key.get_name() != pin.key_type or key.get_base64() != pin.key_data:
            fail('host_key_changed', 'SSH host key changed; verify and trust it again')
        if pin.auth_mode == 'password':
            password = decrypt_password(get_settings().secret_key, row.password_encrypted) if row.password_encrypted else None
            if not password:
                fail('authentication_failed', 'Configure a valid SSH password', 503)
            transport.auth_password(row.username, password, fallback=False)
        elif pin.auth_mode == 'tailscale':
            transport.auth_none(row.username)
        else:
            fail('authentication_failed', 'Unsupported authentication mode', 503)
        if not transport.is_authenticated():
            fail('authentication_failed', 'SSH authentication failed', 503)
        client = paramiko.SSHClient()
        # Authentication above is explicit; SSHClient only supplies SFTP/shell APIs.
        client._transport = transport
        try:
            yield client
        finally:
            client.close()


@contextmanager
def remote_deadline(client, timeout=CONNECT_TIMEOUT):
    """Close stalled protocol requests, including Paramiko's unbounded event waits."""
    expired = threading.Event()
    transport = client.get_transport()
    def abort():
        expired.set()
        transport.close()
    timer = threading.Timer(timeout, abort)
    timer.daemon = True
    timer.start()
    try:
        yield
    finally:
        timer.cancel()
        if expired.is_set():
            fail('remote_timeout', 'Remote operation timed out', 503)
