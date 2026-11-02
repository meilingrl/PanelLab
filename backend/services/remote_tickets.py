"""60-second one-use terminal capabilities. Single-process deployment only."""
import secrets
import threading
import time
from models.user import User
from services.remote_connection import fail, require_pin, target

_tickets = {}
_lock = threading.Lock()
TTL = 60
MAX_TICKETS = 4096


def binding(row, pin):
    return {**target(row), 'key': pin.key_data, 'key_type': pin.key_type, 'auth_mode': pin.auth_mode}


def issue_ticket(db, user, server_id, cwd=None):
    row, pin = require_pin(db, user, server_id)
    if cwd is not None and (not cwd.startswith('/') or any(c in cwd for c in ('\0', '\n', '\r')) or '..' in cwd.split('/')):
        fail('invalid_path', 'Terminal directory must be absolute without control characters or parent traversal', 400)
    now = time.time()
    with _lock:
        for key in list(_tickets):
            if _tickets[key]['expires_at'] <= now:
                del _tickets[key]
        if len(_tickets) >= MAX_TICKETS:
            fail('ticket_capacity', 'Too many pending terminal sessions; retry shortly', 503)
        token = secrets.token_urlsafe(32)
        expires = int(now) + TTL
        _tickets[token] = {'owner': user.id, 'server': server_id, 'cwd': cwd,
                           'binding': binding(row, pin), 'expires_at': expires}
    return {'ticket': token, 'expires_at': expires}


def consume_ticket(db, ticket, server_id):
    with _lock:
        value = _tickets.pop(ticket, None)
    if not value or value['expires_at'] <= time.time() or value['server'] != server_id:
        fail('invalid_terminal_ticket', 'Terminal ticket is invalid or expired; open a new session', 401)
    user = db.get(User, value['owner'])
    if user is None:
        fail('invalid_terminal_ticket', 'Terminal ticket owner no longer exists', 401)
    row, pin = require_pin(db, user, server_id)
    if binding(row, pin) != value['binding']:
        fail('target_changed', 'Connection changed after terminal ticket was issued')
    return user, server_id, value['cwd']
