"""Monitor's remote view reuses pinned shared servers; the legacy unpinned SSH path is gone."""
import pathlib
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from config import get_settings
from database import SessionLocal
from models.monitor_remote import MonitorRemoteConfig
from models.remote_connection import RemoteConnection
from models.server import Server
from models.user import User
from routers.auth import _create_token
from utils.cipher import encrypt_password

TICKS = ('user', 'nice', 'system', 'idle', 'iowait', 'irq', 'softirq', 'steal')


@pytest.fixture
def api(client):
    with SessionLocal() as db:
        db.add(User(id=2, username='other', password_hash='unused'))
        db.add(Server(id=71, user_id=1, name='trainer', host='trainer.invalid', port=22, username='lab'))
        db.add(Server(id=73, user_id=1, name='unpinned', host='web.invalid', port=22, username='lab'))
        db.add(Server(id=72, user_id=2, name='not mine', host='other.invalid', port=22, username='lab'))
        db.add(RemoteConnection(server_id=71, owner_id=1, host='trainer.invalid', port=22, username='lab',
                                key_type='ssh-ed25519', key_data='AAAA', fingerprint='SHA256:x', auth_mode='key'))
        db.commit()
    client.headers['Authorization'] = 'Bearer ' + _create_token('admin')
    from services import remote_monitor
    remote_monitor.reset_samples()
    return client


def snapshot(boot='boot-a', clock=100.0, busy=0, idle=0, rx=0, tx=0):
    ticks = dict.fromkeys(TICKS, 0)
    ticks.update(user=busy, idle=idle)
    return {'observed_at': '2026-11-10T00:00:00+00:00', 'warnings': ['GPU unavailable'],
            'sampling': {'boot_id': boot, 'monotonic_seconds': clock},
            'system': {'cpu': {'percent': None, 'load_average': [0.5, 0.4, 0.3], 'ticks': ticks},
                       'memory': {'total_bytes': 8 * 1024 ** 3, 'available_bytes': 2 * 1024 ** 3},
                       'network': [{'name': 'lo', 'received_bytes': 10 ** 9, 'sent_bytes': 10 ** 9, 'ifindex': 1},
                                   {'name': 'eth0', 'received_bytes': rx, 'sent_bytes': tx, 'ifindex': 2},
                                   {'name': 'eth1', 'received_bytes': None, 'sent_bytes': None, 'ifindex': 3}],
                       'disk': [{'mountpoint': '/data', 'total_bytes': 100 * 1024 ** 3, 'available_bytes': 25 * 1024 ** 3,
                                 'used_bytes': 70 * 1024 ** 3}],
                       'gpu': [{'index': 0, 'name': 'A100', 'memory_used_bytes': 1000 * 1048576, 'memory_total_bytes': 40000 * 1048576, 'utilization_percent': 87}]},
            'processes': [], 'capabilities': {}, 'target_process': None}


@pytest.fixture
def remote(monkeypatch):
    from services import remote_monitor
    calls, queue = [], []
    @contextmanager
    def opened(db, user, server_id):
        calls.append(server_id)
        yield SimpleNamespace()
    monkeypatch.setattr(remote_monitor, 'open_remote', opened)
    monkeypatch.setattr(remote_monitor, 'collect_snapshot', lambda client: queue.pop(0))
    return calls, queue


def test_hosts_list_local_and_owned_servers_with_trust_state(api):
    hosts = api.get('/api/monitor/hosts').json()['hosts']
    assert hosts[0] == {'id': 'local', 'label': '本机', 'pinned': True}
    by_id = {h['id']: h for h in hosts}
    assert by_id['server:71']['pinned'] is True and 'trainer' in by_id['server:71']['label']
    assert by_id['server:73']['pinned'] is False
    assert 'server:72' not in by_id and 'remote' not in by_id


@pytest.mark.parametrize('target, status, code', [
    ('server:72', 404, 'server_not_found'), ('server:999', 404, 'server_not_found'),
    ('server:73', 409, 'trust_required'), ('remote', 410, 'legacy_remote_removed'),
    ('server:abc', 422, 'invalid_target'), ('elsewhere', 422, 'invalid_target'),
])
def test_stats_target_boundaries_never_open_ssh(api, remote, target, status, code):
    calls, _ = remote
    result = api.get('/api/monitor/stats', params={'target': target})
    assert result.status_code == status and result.json()['detail']['code'] == code
    assert calls == []


def test_remote_stats_shape_rates_and_baseline_resets(api, remote):
    calls, queue = remote
    queue += [snapshot(clock=100, busy=100, idle=900, rx=1000, tx=2000),
              snapshot(clock=105, busy=400, idle=1600, rx=1000 + 5 * 2048, tx=2000 + 5 * 1024),
              snapshot(boot='boot-b', clock=3, busy=10, idle=10, rx=5, tx=5)]
    first = api.get('/api/monitor/stats', params={'target': 'server:71'}).json()
    assert calls == [71]
    assert first['cpu_percent'] is None
    assert first['memory'] == {'total_mb': 8192.0, 'available_mb': 2048.0, 'used_mb': 6144.0, 'percent': 75.0}
    assert first['disk'] == [{'mountpoint': '/data', 'total_gb': 100.0, 'used_gb': 70.0, 'free_gb': 25.0, 'percent': 73.7}]
    assert first['network']['bytes_recv'] == 1000 and first['network']['bytes_sent'] == 2000
    assert first['network']['rate_recv_kbps'] is None and first['network']['network_history'] == []
    assert first['gpu'][0]['utilization_percent'] == 87 and first['load_average'] == [0.5, 0.4, 0.3]
    assert first['warnings'] == ['GPU unavailable'] and first['observed_at']
    second = api.get('/api/monitor/stats', params={'target': 'server:71'}).json()
    assert second['cpu_percent'] == 30.0
    assert second['network']['rate_recv_kbps'] == 2.0 and second['network']['rate_sent_kbps'] == 1.0
    assert len(second['network']['network_history']) == 1
    rebooted = api.get('/api/monitor/stats', params={'target': 'server:71'}).json()
    assert rebooted['cpu_percent'] is None and rebooted['network']['rate_recv_kbps'] is None
    assert rebooted['network']['network_history'] == []


def test_samples_are_per_user_and_server(api, remote):
    from services import remote_monitor
    _, queue = remote
    queue += [snapshot(clock=100, busy=100, idle=900)]
    api.get('/api/monitor/stats', params={'target': 'server:71'})
    assert set(remote_monitor._samples) == {(1, 71)}


def test_legacy_config_is_reported_and_deletable_but_never_used(api, remote):
    with SessionLocal() as db:
        db.add(MonitorRemoteConfig(id=1, user_id=1, host='old.invalid', port=22, username='root',
                                   password_encrypted=encrypt_password(get_settings().secret_key, 'OLD_SECRET')))
        db.commit()
    legacy = api.get('/api/monitor/remote-config').json()
    assert legacy == {'legacy_configured': True, 'host': 'old.invalid', 'port': 22, 'username': 'root'}
    assert 'OLD_SECRET' not in str(legacy)
    assert api.put('/api/monitor/remote-config', json={'host': 'x', 'port': 22, 'username': 'r', 'password': 'p'}).status_code == 405
    assert api.post('/api/monitor/remote-install-psutil').status_code == 404
    assert api.delete('/api/monitor/remote-config').json() == {'legacy_configured': False}
    with SessionLocal() as db:
        assert db.query(MonitorRemoteConfig).count() == 0
    assert remote[0] == []


def test_no_unpinned_host_key_policy_remains():
    source = pathlib.Path(__file__).resolve().parents[1]
    offenders = [str(p) for p in source.rglob('*.py')
                 if 'tests' not in p.parts and '.venv' not in str(p) and 'AutoAddPolicy' in p.read_text(errors='ignore')]
    assert offenders == []


def test_out_of_order_sample_is_ignored_not_reset(api, remote):
    _, queue = remote
    queue += [snapshot(clock=100, busy=100, idle=900, rx=0), snapshot(clock=105, busy=200, idle=1800, rx=5120),
              snapshot(clock=110, busy=300, idle=2700, rx=10240), snapshot(clock=104, busy=150, idle=1500, rx=4096),
              snapshot(clock=115, busy=400, idle=3600, rx=15360)]
    for _ in range(3): api.get('/api/monitor/stats', params={'target': 'server:71'})
    stale = api.get('/api/monitor/stats', params={'target': 'server:71'}).json()
    assert stale['network']['rate_recv_kbps'] is None and len(stale['network']['network_history']) == 2
    after = api.get('/api/monitor/stats', params={'target': 'server:71'}).json()
    assert after['network']['rate_recv_kbps'] == 1.0 and len(after['network']['network_history']) == 3


def test_unicode_digit_target_and_odd_interface_names_do_not_500(api, remote):
    _, queue = remote
    assert api.get('/api/monitor/stats', params={'target': 'server:²'}).json()['detail']['code'] == 'invalid_target'
    odd = snapshot(clock=100, rx=1)
    odd['system']['network'] += [{'name': 7, 'received_bytes': 1, 'sent_bytes': 1, 'ifindex': None},
                                 {'name': 'eth0', 'received_bytes': 1, 'sent_bytes': 1, 'ifindex': None}]
    queue.append(odd)
    assert api.get('/api/monitor/stats', params={'target': 'server:71'}).status_code == 200
