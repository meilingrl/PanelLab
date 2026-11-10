"""Monitor page view of a pinned shared server, built on the workspace collector."""
import threading
from collections import deque

from services.remote_connection import open_remote, require_pin
from services.remote_probe import CPU_FIELDS, collect_snapshot

MAX_SAMPLE_GAP = 300  # seconds; older baselines are not used for rates
HISTORY = 60
_samples = {}
_lock = threading.Lock()
MIB = 1024 * 1024
GIB = 1024 ** 3


def reset_samples():
    with _lock:
        _samples.clear()


def _number(value):
    return type(value) is int and value >= 0


def _memory(memory):
    if not isinstance(memory, dict) or not _number(memory.get('total_bytes')) or not memory['total_bytes']:
        return None
    total, available = memory['total_bytes'], memory.get('available_bytes')
    if not _number(available) or available > total:
        return None
    used = total - available
    return {'total_mb': round(total / MIB, 1), 'available_mb': round(available / MIB, 1),
            'used_mb': round(used / MIB, 1), 'percent': round(used * 100 / total, 1)}


def _disk(disks):
    result = []
    for item in disks or []:
        total, free, used = item.get('total_bytes'), item.get('available_bytes'), item.get('used_bytes')
        if not all(_number(v) for v in (total, free, used)) or not total:
            continue
        # Same basis as df: used / (used + available), ignoring root-reserved blocks.
        basis = used + free
        result.append({'mountpoint': item['mountpoint'], 'total_gb': round(total / GIB, 2), 'used_gb': round(used / GIB, 2),
                       'free_gb': round(free / GIB, 2), 'percent': round(used * 100 / basis, 1) if basis else None})
    return result


def _network_totals(network):
    if not isinstance(network, list):
        return None
    usable = [n for n in network if isinstance(n, dict) and isinstance(n.get('name'), str) and n['name'] != 'lo'
              and _number(n.get('received_bytes')) and _number(n.get('sent_bytes'))]
    if not usable:
        return None
    interfaces = tuple(sorted((n['name'], str(n.get('ifindex'))) for n in usable))
    return interfaces, sum(n['received_bytes'] for n in usable), sum(n['sent_bytes'] for n in usable)


def stats(db, user, server_id):
    # Ownership and trust are refused before any network connection is attempted.
    require_pin(db, user, server_id)
    with open_remote(db, user, server_id) as client:
        snap = collect_snapshot(client)
    system, sampling = snap['system'], snap['sampling']
    cpu = system.get('cpu') or {}
    ticks = cpu.get('ticks')
    totals = _network_totals(system.get('network'))
    boot, clock = sampling.get('boot_id'), sampling.get('monotonic_seconds')
    key = (user.id, server_id)
    cpu_percent = rate_recv = rate_sent = None
    with _lock:
        previous = _samples.get(key)
        same_boot = bool(previous and boot and clock is not None and previous['boot'] == boot)
        if same_boot and clock <= previous['clock']:
            # Concurrent polls (two tabs) can finish out of order: report the older sample as-is,
            # but keep the newer baseline and history.
            history_list = list(previous['history'])
        else:
            comparable = same_boot and clock - previous['clock'] <= MAX_SAMPLE_GAP
            history = previous['history'] if comparable else deque(maxlen=HISTORY)
            if comparable and ticks and previous['ticks']:
                deltas = [ticks[k] - previous['ticks'][k] for k in CPU_FIELDS]
                total = sum(deltas)
                if total > 0 and min(deltas) >= 0:
                    idle = deltas[CPU_FIELDS.index('idle')] + deltas[CPU_FIELDS.index('iowait')]
                    cpu_percent = round((total - idle) * 100 / total, 1)
            if comparable and totals and previous['network'] and previous['network'][0] == totals[0]:
                interval = clock - previous['clock']
                received, sent = totals[1] - previous['network'][1], totals[2] - previous['network'][2]
                if received >= 0 and sent >= 0:
                    rate_recv, rate_sent = round(received / 1024 / interval, 2), round(sent / 1024 / interval, 2)
                    # The remote clock is monotonic, not wall time; label points by the observation time.
                    history.append({'observed_at': snap['observed_at'], 'rate_recv_kbps': rate_recv, 'rate_sent_kbps': rate_sent})
            if boot and clock is not None:
                _samples[key] = {'boot': boot, 'clock': clock, 'ticks': ticks, 'network': totals, 'history': history}
            else:
                _samples.pop(key, None)
            history_list = list(history)
    network = None
    if totals:
        network = {'bytes_recv': totals[1], 'bytes_sent': totals[2], 'rate_recv_kbps': rate_recv,
                   'rate_sent_kbps': rate_sent, 'network_history': history_list}
    return {'target': f'server:{server_id}', 'observed_at': snap['observed_at'], 'cpu_percent': cpu_percent,
            'load_average': cpu.get('load_average'), 'memory': _memory(system.get('memory')),
            'disk': _disk(system.get('disk')), 'network': network, 'gpu': system.get('gpu'),
            'warnings': snap['warnings']}
