"""Fixed Linux/Python3 collector; no client-provided executable or command."""
import json
import shlex
import time
from services.remote_connection import CONNECT_TIMEOUT, fail, observed_at, remote_deadline

COLLECTOR_TIMEOUT = 20
OUTPUT_LIMIT = 1024 * 1024

COLLECTOR_SCRIPT = r'''
import json, os, subprocess

def text(path, limit=65536):
    with open(path, 'r', errors='replace') as f:
        return f.read(limit)

def kib_fields(path):
    out = {}
    for line in text(path).splitlines():
        bits = line.split()
        if len(bits) >= 2 and bits[1].isdigit():
            out[bits[0].rstrip(':')] = int(bits[1]) * 1024
    return out

result = {'system': {'cpu': None, 'memory': None, 'network': None, 'gpu': None},
          'processes': [], 'capabilities': {}, 'warnings': []}
try:
    result['system']['cpu'] = {'percent': None, 'load_average': [float(x) for x in text('/proc/loadavg').split()[:3]]}
    mem = kib_fields('/proc/meminfo')
    result['system']['memory'] = {'total_bytes': mem.get('MemTotal'), 'available_bytes': mem.get('MemAvailable')}
except (OSError, ValueError):
    result['warnings'].append('CPU or memory unavailable')
try:
    network = []
    for line in text('/proc/net/dev').splitlines():
        if ':' not in line: continue
        name, values = line.split(':', 1)
        values = values.split()
        network.append({'name': name.strip(), 'received_bytes': int(values[0]), 'sent_bytes': int(values[8])})
    result['system']['network'] = network[:200]
except (OSError, ValueError, IndexError):
    result['warnings'].append('Network counters unavailable')
try:
    boot = text('/proc/sys/kernel/random/boot_id').strip()
    pids = sorted((int(p) for p in os.listdir('/proc') if p.isdigit()))
    for pid in pids[:200]:
        try:
            base = '/proc/' + str(pid)
            fields = text(base + '/stat').rsplit(')', 1)[1].split()
            identity = boot + ':' + fields[19]
            with open(base + '/cmdline', 'rb') as f:
                command = f.read(2048).replace(b'\0', b' ').decode('utf-8', errors='replace').strip()[:512]
            entry = {'pid': pid, 'identity': identity, 'command': command, 'cpu': None}
            try: entry['cwd'] = os.readlink(base + '/cwd')[:4096]
            except OSError: pass
            try: entry['memory_bytes'] = kib_fields(base + '/status').get('VmRSS')
            except OSError: pass
            # Reject a PID recycled while its command/resource fields were read.
            if text(base + '/stat').rsplit(')', 1)[1].split()[19] != fields[19]: continue
            result['processes'].append(entry)
        except (OSError, ValueError, IndexError):
            continue
    result['capabilities']['processes'] = True
    result['capabilities']['processes_truncated'] = len(pids) > 200
except (OSError, ValueError):
    result['capabilities']['processes'] = False
    result['warnings'].append('Process identity unavailable')
try:
    gpu = subprocess.run(['nvidia-smi', '--query-gpu=index,name,memory.used,memory.total,utilization.gpu', '--format=csv,noheader,nounits'],
                         capture_output=True, text=True, timeout=3, check=True)
    rows = []
    for line in gpu.stdout[:65536].splitlines()[:64]:
        parts = [p.strip() for p in line.split(',')]
        if len(parts) == 5:
            rows.append({'index': int(parts[0]), 'name': parts[1][:128], 'memory_used_bytes': int(parts[2])*1048576,
                         'memory_total_bytes': int(parts[3])*1048576, 'utilization_percent': float(parts[4])})
    result['system']['gpu'] = rows
except (OSError, ValueError, subprocess.SubprocessError):
    result['warnings'].append('GPU unavailable')
for module, data in result['system'].items():
    result['capabilities'][module] = data is not None
print(json.dumps(result))
'''


def parse_snapshot(output):
    try:
        value = json.loads(output)
        assert isinstance(value, dict) and isinstance(value['system'], dict)
        assert all(k in value['system'] for k in ('cpu', 'memory', 'network', 'gpu'))
        assert isinstance(value['processes'], list)
        assert isinstance(value['capabilities'], dict) and isinstance(value['warnings'], list)
        processes = []
        for p in value['processes'][:200]:
            assert isinstance(p, dict) and isinstance(p['pid'], int) and p['pid'] > 0
            assert isinstance(p['identity'], str) and 0 < len(p['identity']) <= 128
            assert isinstance(p['command'], str)
            entry = {'pid': p['pid'], 'identity': p['identity'], 'command': p['command'][:512], 'cpu': None}
            if isinstance(p.get('cwd'), str): entry['cwd'] = p['cwd'][:4096]
            if isinstance(p.get('memory_bytes'), int) and p['memory_bytes'] >= 0: entry['memory_bytes'] = p['memory_bytes']
            processes.append(entry)
        return {'observed_at': observed_at(), 'system': value['system'], 'processes': processes,
                'capabilities': value['capabilities'], 'warnings': [str(w)[:512] for w in value['warnings'][:32]]}
    except (ValueError, TypeError, KeyError, AssertionError):
        fail('collector_invalid', 'Remote collector returned invalid data', 503)


def collect_snapshot(client):
    channel = client.get_transport().open_session(timeout=CONNECT_TIMEOUT)
    deadline = time.monotonic() + COLLECTOR_TIMEOUT
    output = bytearray()
    total = 0
    try:
        channel.settimeout(CONNECT_TIMEOUT)
        with remote_deadline(client):
            channel.exec_command('python3 -c ' + shlex.quote(COLLECTOR_SCRIPT))
        while True:
            if time.monotonic() >= deadline:
                fail('collector_timeout', 'Remote observation timed out', 503)
            if channel.recv_ready():
                data = channel.recv(65536)
                output.extend(data)
                total += len(data)
            if channel.recv_stderr_ready():
                total += len(channel.recv_stderr(65536))
            if total > OUTPUT_LIMIT:
                fail('collector_output_limit', 'Remote observation exceeded output limit', 503)
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                break
            time.sleep(0.005)
        if channel.recv_exit_status() != 0:
            fail('collector_unavailable', 'Linux Python3 collector unavailable', 503)
        return parse_snapshot(bytes(output))
    finally:
        channel.close()
