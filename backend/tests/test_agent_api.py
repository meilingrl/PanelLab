"""Agent HTTP routes: owner scoping, op mapping, no secret echo."""
import json
from contextlib import contextmanager

import pytest

from database import SessionLocal
from models.server import Server
from models.user import User
from routers.auth import _create_token
from services import agent_client


@pytest.fixture
def remote(client):
    with SessionLocal() as db:
        db.add(Server(id=71, user_id=1, name='f', host='f.invalid', port=22, username='r'))
        db.add(User(id=2, username='other', password_hash='unused'))
        db.add(Server(id=72, user_id=2, name='o', host='o.invalid', port=22, username='r'))
        db.commit()
    client.headers['Authorization'] = 'Bearer ' + _create_token('admin')
    return client


@pytest.fixture
def calls(monkeypatch):
    log = []

    def fake_call(db, user, server_id, op, args=None):
        log.append((server_id, op, args))
        return {'echo': op}
    monkeypatch.setattr(agent_client, 'call', fake_call)
    monkeypatch.setattr(agent_client, 'upload_bundle', lambda db, user, sid: log.append((sid, 'install', None)) or {'path': '/p', 'command': 'c', 'version': '0'})
    return log


B = '/api/servers/71/agent'


def test_routes_map_to_ops(remote, calls):
    job = {'name': 'j'}
    cases = [
        ('get', '', None, 'status', None),
        ('get', '/jobs', None, 'jobs.list', None),
        ('post', '/jobs', {'job': job, 'secret_env': {'K': 'v'}}, 'jobs.create', {'job': job, 'secret_env': {'K': 'v'}}),
        ('post', '/jobs', {'job': job}, 'jobs.create', {'job': job}),
        ('get', '/jobs/a-b1', None, 'jobs.get', {'id': 'a-b1'}),
        ('put', '/jobs/a-b1', {'revision': 3, 'changes': {'x': 1}, 'secret_env': {'K': None}}, 'jobs.update',
         {'id': 'a-b1', 'revision': 3, 'changes': {'x': 1}, 'secret_env': {'K': None}}),
        ('delete', '/jobs/a-b1', None, 'jobs.delete', {'id': 'a-b1'}),
        ('post', '/jobs/a-b1/run', None, 'jobs.run_now', {'id': 'a-b1'}),
        ('post', '/jobs/a-b1/pause', None, 'jobs.pause', {'id': 'a-b1'}),
        ('post', '/jobs/a-b1/resume', None, 'jobs.resume', {'id': 'a-b1'}),
        ('get', '/jobs/a-b1/runs', None, 'runs.list', {'job_id': 'a-b1'}),
        ('get', '/jobs/a-b1/runs?status=failed&before=9', None, 'runs.list', {'job_id': 'a-b1', 'status': 'failed', 'before': 9}),
        ('get', '/runs/5/output', None, 'runs.output', {'run_id': 5, 'offset': 0}),
        ('get', '/runs/5/output?offset=100', None, 'runs.output', {'run_id': 5, 'offset': 100}),
        ('post', '/runs/5/cancel', None, 'runs.cancel', {'run_id': 5}),
        ('post', '/schedule-preview', {'schedule': {'cron': ['* * * * *']}}, 'jobs.preview', {'schedule': {'cron': ['* * * * *']}}),
        ('get', '/alerts', None, 'alerts.get', None),
        ('put', '/alerts', {'smtp': None, 'ssh_enabled': True}, 'alerts.configure', {'smtp': None, 'ssh_enabled': True}),
        ('post', '/alerts/test', None, 'alerts.test_email', None),
    ]
    for method, path, body, op, args in cases:
        calls.clear()
        kw = {'json': body} if body is not None else {}
        r = getattr(remote, method)(B + path, **kw)
        assert r.status_code == 200, (method, path, r.text)
        assert r.json() == {'echo': op}
        assert calls == [(71, op, args)], (method, path, calls)
    calls.clear()
    r = remote.post(B + '/install')
    assert r.status_code == 200 and r.json()['path'] == '/p'
    assert calls == [(71, 'install', None)]


def test_routes_require_owner(remote, calls):
    paths = [('get', ''), ('get', '/jobs'), ('post', '/jobs'), ('put', '/alerts'), ('post', '/install'),
             ('get', '/runs/1/output'), ('delete', '/jobs/a')]
    saved = remote.headers.pop('Authorization')
    for m, p in paths:
        assert getattr(remote, m)(B + p).status_code == 401
    remote.headers['Authorization'] = saved
    assert not calls


def test_other_users_server_is_404(remote, monkeypatch):
    # real call(): ownership is enforced by open_remote before anything touches SSH
    r = remote.get('/api/servers/72/agent')
    assert r.status_code == 404
    assert remote.get('/api/servers/999/agent').status_code == 404


def test_job_id_and_run_id_validation(remote, calls):
    for bad in ('A', '-a', 'a_b', 'a' * 49, 'a.b'):
        r = remote.get(f'{B}/jobs/{bad}')
        assert r.status_code == 422 and r.json()['detail']['code'] == 'invalid_job_id', bad
    assert remote.get(f'{B}/jobs/' + 'a' * 48).status_code == 200
    assert remote.get(f'{B}/runs/0/output').status_code == 422
    assert remote.get(f'{B}/runs/abc/output').status_code == 422
    assert remote.post(f'{B}/runs/-1/cancel').status_code == 422
    assert remote.get(f'{B}/runs/1/output?offset=-1').status_code == 422
    assert remote.get(f'{B}/jobs/a/runs?before=0').status_code == 422


def test_bad_bodies_are_422_without_echo(remote, calls):
    for method, path in [('post', '/jobs'), ('put', '/jobs/a'), ('put', '/alerts'), ('post', '/schedule-preview')]:
        payloads = ['["TOPSECRET"]', '"TOPSECRET"', '{"TOPSECRET": ']
        if path != '/alerts':
            payloads.append('{"job": "TOPSECRET"}')  # /alerts forwards any object; the Agent rejects unknown fields
        for payload in payloads:
            r = getattr(remote, method)(B + path, content=payload, headers={'content-type': 'application/json'})
            assert r.status_code == 422, (method, path, payload, r.text)
            assert 'TOPSECRET' not in r.text
    assert not calls


def test_secret_fields_not_echoed_on_validation_error(remote, calls):
    r = remote.post(B + '/jobs', json={'job': 'x', 'secret_env': {'API_KEY': 'TOPSECRET'}})
    assert r.status_code == 422 and 'TOPSECRET' not in r.text
    r = remote.put(B + '/alerts', json={'smtp': {'password': 'TOPSECRET'}, 'bogus': 1})
    assert 'TOPSECRET' not in r.text
    r = remote.put(B + '/jobs/a', json={'revision': 'bad', 'changes': 1, 'secret_env': {'K': 'TOPSECRET'}})
    assert r.status_code == 422 and 'TOPSECRET' not in r.text
