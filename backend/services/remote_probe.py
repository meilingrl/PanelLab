"""Fixed Linux/Python3 collector; no client-provided executable or command."""
import json
import math
import shlex
import time
from services.remote_connection import CONNECT_TIMEOUT, fail, observed_at, remote_deadline

COLLECTOR_TIMEOUT = 20
OUTPUT_LIMIT = 1024 * 1024

COLLECTOR_SCRIPT = r'''
import json, math, os, subprocess, sys, itertools, time
args = json.loads(sys.argv[1]) if len(sys.argv) == 2 and sys.argv[1].startswith("{") else {}
target_pid = args.get("pid")
query = (args.get("query") or "").casefold()

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

MAX_SAFE_INTEGER = 2**53 - 1
CPU_FIELDS = ('user', 'nice', 'system', 'idle', 'iowait', 'irq', 'softirq', 'steal')

def counter(value):
    value = int(value)
    if not 0 <= value <= MAX_SAFE_INTEGER: raise ValueError()
    return value

result = {'system': {'cpu': {'percent': None, 'load_average': None, 'ticks': None}, 'memory': None, 'network': None, 'gpu': None},
          'sampling': {'boot_id': None, 'monotonic_seconds': None},
          'processes': [], 'capabilities': {}, 'warnings': []}
try:
    boot = text('/proc/sys/kernel/random/boot_id', 130).strip()
    if not 0 < len(boot) <= 128 or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in boot): raise ValueError()
    result['sampling']['boot_id'] = boot
except (OSError, ValueError):
    result['warnings'].append('Boot identity unavailable')
# One remote clock sample next to the CPU/network counter reads. No wait or rate inference.
try:
    sample_time = time.monotonic()
    if type(sample_time) not in (int, float) or not math.isfinite(sample_time) or sample_time < 0: raise ValueError()
    result['sampling']['monotonic_seconds'] = float(sample_time)
except (OSError, ValueError, OverflowError):
    result['warnings'].append('Remote monotonic time unavailable')
try:
    load = [float(x) for x in text('/proc/loadavg').split()[:3]]
    if len(load) != 3 or not all(math.isfinite(x) and x >= 0 for x in load): raise ValueError()
    result['system']['cpu']['load_average'] = load
except (OSError, ValueError):
    result['warnings'].append('CPU load unavailable')
try:
    fields = text('/proc/stat').splitlines()[0].split()
    if len(fields) < 9 or fields[0] != 'cpu': raise ValueError()
    result['system']['cpu']['ticks'] = dict(zip(CPU_FIELDS, [counter(x) for x in fields[1:9]]))
except (OSError, ValueError, IndexError):
    result['warnings'].append('CPU counters unavailable')
try:
    mem = kib_fields('/proc/meminfo')
    result['system']['memory'] = {'total_bytes': mem.get('MemTotal'), 'available_bytes': mem.get('MemAvailable')}
except (OSError, ValueError):
    result['warnings'].append('Memory unavailable')
try:
    network = []
    for line in text('/proc/net/dev').splitlines():
        if ':' not in line: continue
        name, values = line.split(':', 1)
        values = values.split()
        name = name.strip()
        ifindex = None
        try:
            if not 0 < len(name.encode('utf-8')) <= 15 or name in ('.', '..') or any(c.isspace() or c in '/:' or ord(c) < 32 or ord(c) == 127 for c in name): raise ValueError()
            index = text('/sys/class/net/' + name + '/ifindex', 33).strip()
            if not index.isascii() or not index.isdigit(): raise ValueError()
            ifindex = counter(index)
            if ifindex == 0: raise ValueError()
        except (OSError, ValueError):
            ifindex = None
            result['warnings'].append('Network interface identity unavailable')
        entry = {'name': name[:128], 'received_bytes': None, 'sent_bytes': None, 'ifindex': ifindex}
        for key, index in (('received_bytes', 0), ('sent_bytes', 8)):
            try: entry[key] = counter(values[index])
            except (ValueError, IndexError): result['warnings'].append('Network counter unavailable')
        network.append(entry)
        if len(network) >= 200: break
    result['system']['network'] = network[:200]
except (OSError, ValueError, IndexError):
    result['warnings'].append('Network counters unavailable')
def absence_verifiable():
    # ENOENT is ambiguous on hidepid procfs. Read the collector's own mount
    # namespace, conservatively refusing absence claims for unknown/truncated
    # metadata, non-proc/partial mounts, or any restricted hidepid mode.
    try:
        mountinfo = text('/proc/self/mountinfo', 65537)
        if len(mountinfo) > 65536: return False
        matches = []
        for line in mountinfo.splitlines():
            left, separator, right = line.partition(' - ')
            fields, filesystem = left.split(), right.split()
            if len(fields) < 6 or fields[4] != '/proc': continue
            if not separator or len(filesystem) < 3 or filesystem[0] != 'proc' or fields[3] != '/': return False
            options = fields[5].split(',') + filesystem[2].split(',')
            matches.append(all(option in ('hidepid=0', 'hidepid=off') for option in options if option.startswith('hidepid=')))
        return len(matches) == 1 and matches[0]
    except (OSError, ValueError): return False

can_confirm_absence = absence_verifiable()
result['capabilities']['process_absence_verifiable'] = can_confirm_absence
if not can_confirm_absence:
    result['warnings'].append('Process visibility is restricted or unknown; missing PIDs are inconclusive')

def process(pid, boot):
    base = '/proc/' + str(pid)
    # Absence is evidence only on verified unrestricted procfs. Permission,
    # hidden PID, read and identity races remain unavailable.
    try: fields = text(base + '/stat').rsplit(')', 1)[1].split()
    except FileNotFoundError:
        return {'pid': pid, 'status': 'missing' if can_confirm_absence else 'unavailable', 'process': None}
    identity = boot + ':' + fields[19]
    with open(base + '/cmdline', 'rb') as f:
        command = f.read(2048).replace(b'\0', b' ').decode('utf-8', errors='replace').strip()[:512]
    entry = {'pid': pid, 'identity': identity, 'command': command, 'cpu': None, 'state': fields[0]}
    try: entry['cwd'] = os.readlink(base + '/cwd')[:4096]
    except OSError: pass
    try: entry['memory_bytes'] = kib_fields(base + '/status').get('VmRSS')
    except OSError: pass
    if text(base + '/stat').rsplit(')', 1)[1].split()[19] != fields[19]:
        return {'pid': pid, 'status': 'unavailable', 'process': None}
    return {'pid': pid, 'status': 'present', 'process': entry}

result['target_process'] = None
boot = result['sampling']['boot_id']
if not boot:
    result['warnings'].append('Process identity unavailable')
if target_pid is not None:
    result['target_process'] = {'pid': target_pid, 'status': 'unavailable', 'process': None}
    if boot:
        try: result['target_process'] = process(target_pid, boot)
        except (OSError, ValueError, IndexError): pass
try:
    if not boot: raise ValueError()
    truncated = False
    incomplete = not can_confirm_absence
    with os.scandir('/proc') as entries:
        for index, item in enumerate(entries):
            if index >= 4096:
                truncated = True
                break
            if not item.name.isdigit(): continue
            if len(result['processes']) >= 200:
                truncated = True
                break
            try:
                found = process(int(item.name), boot)
                if found['status'] != 'present':
                    incomplete = incomplete or found['status'] == 'unavailable'
                    continue
                entry = found['process']
                if query and query not in (str(entry['pid']) + ' ' + entry['command'] + ' ' + entry.get('cwd', '')).casefold(): continue
                result['processes'].append(entry)
            except (OSError, ValueError, IndexError): incomplete = True
    result['capabilities']['processes'] = True
    result['capabilities']['processes_truncated'] = truncated
    result['capabilities']['processes_incomplete'] = incomplete
except (OSError, ValueError):
    result['capabilities']['processes'] = False
    result['capabilities']['processes_incomplete'] = True
    result['warnings'].append('Process discovery unavailable')
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
result['capabilities']['cpu'] = any(result['system']['cpu'][key] is not None for key in ('load_average', 'ticks'))
result['warnings'] = list(dict.fromkeys(result['warnings']))[:32]
print(json.dumps(result))
'''

MAX_SAFE_INTEGER = 2**53 - 1
CPU_FIELDS = ('user', 'nice', 'system', 'idle', 'iowait', 'irq', 'softirq', 'steal')


def safe_counter(value):
    return type(value) is int and 0 <= value <= MAX_SAFE_INTEGER


def sampling_evidence(value, warnings):
    """Supplemental evidence never invalidates otherwise useful legacy snapshots."""
    source = value if isinstance(value, dict) else {}
    boot = source.get('boot_id')
    if not (isinstance(boot, str) and 0 < len(boot) <= 128 and
            not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in boot)):
        boot = None
        warnings.append('Boot identity unavailable')
    clock = source.get('monotonic_seconds')
    try:
        valid_clock = type(clock) in (int, float) and math.isfinite(clock) and clock >= 0
    except OverflowError:
        valid_clock = False
    if not valid_clock:
        clock = None
        warnings.append('Remote monotonic time unavailable')
    return {'boot_id': boot, 'monotonic_seconds': float(clock) if clock is not None else None}


def system_evidence(system, warnings):
    system = dict(system)
    cpu = system.get('cpu')
    if isinstance(cpu, dict):
        cpu = dict(cpu)
        ticks = cpu.get('ticks')
        if isinstance(ticks, dict) and all(safe_counter(ticks.get(key)) for key in CPU_FIELDS):
            cpu['ticks'] = {key: ticks[key] for key in CPU_FIELDS}
        else:
            cpu['ticks'] = None
            warnings.append('CPU counters unavailable')
        cpu['percent'] = None
        system['cpu'] = cpu
    network = system.get('network')
    if isinstance(network, list):
        entries = []
        for item in network[:200]:
            if not isinstance(item, dict):
                warnings.append('Network counter unavailable')
                continue
            entry = dict(item)
            for key in ('received_bytes', 'sent_bytes', 'ifindex'):
                value = entry.get(key)
                if not safe_counter(value) or (key == 'ifindex' and value == 0):
                    entry[key] = None
                    warnings.append('Network interface identity unavailable' if key == 'ifindex' else 'Network counter unavailable')
            entries.append(entry)
        system['network'] = entries
    return system


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
            if isinstance(p.get('state'), str) and len(p['state']) == 1: entry['state'] = p['state']
            if isinstance(p.get('cwd'), str): entry['cwd'] = p['cwd'][:4096]
            if isinstance(p.get('memory_bytes'), int) and p['memory_bytes'] >= 0: entry['memory_bytes'] = p['memory_bytes']
            processes.append(entry)
        target = value.get('target_process')
        if target is not None:
            assert isinstance(target, dict) and type(target['pid']) is int and target['pid'] > 0
            assert target['status'] in ('present', 'missing', 'unavailable')
            if target['status'] == 'present':
                proc = target['process']
                assert proc['pid'] == target['pid']
                # Reuse the same bounded process parser for the independent lookup.
                target['process'] = parse_snapshot(json.dumps({**value, 'processes':[proc], 'target_process':None}).encode())['processes'][0]
            else: target['process'] = None
        warnings = [str(w)[:512] for w in value['warnings'][:32]]
        sampling = sampling_evidence(value.get('sampling'), warnings)
        system = system_evidence(value['system'], warnings)
        return {'observed_at': observed_at(), 'system': system, 'sampling': sampling, 'processes': processes,
                'target_process': target, 'capabilities': value['capabilities'], 'warnings': list(dict.fromkeys(warnings))[:32]}
    except (ValueError, TypeError, KeyError, AssertionError):
        fail('collector_invalid', 'Remote collector returned invalid data', 503)


def collect_snapshot(client, *, pid=None, query=None):
    if pid is not None and (type(pid) is not int or not 0 < pid <= 2147483647):
        fail('invalid_pid', 'PID must be a positive integer', 422)
    if query is not None and (not isinstance(query, str) or len(query) > 128 or any(ord(c) < 32 for c in query)):
        fail('invalid_query', 'Process query must be at most 128 characters without controls', 422)
    channel = client.get_transport().open_session(timeout=CONNECT_TIMEOUT)
    deadline = time.monotonic() + COLLECTOR_TIMEOUT
    output = bytearray()
    total = 0
    try:
        channel.settimeout(CONNECT_TIMEOUT)
        with remote_deadline(client):
            channel.exec_command('python3 -c ' + shlex.quote(COLLECTOR_SCRIPT) + ' ' + shlex.quote(json.dumps({'pid': pid, 'query': query})))
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
