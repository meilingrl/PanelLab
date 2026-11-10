"""Read-only collector and SFTP boundaries with controlled Linux fixture inputs."""
import json
import stat
from types import SimpleNamespace
import pytest
from fastapi import HTTPException


class SFTP:
    def normalize(self, path): return '/home/runner' if path == '.' else path
    def lstat(self, path):
        if path == '/home/runner/link': mode = stat.S_IFLNK
        elif path.endswith('result 中文\'s.txt'): mode = stat.S_IFREG
        else: mode = stat.S_IFDIR
        return SimpleNamespace(st_mode=mode, st_size=12, st_mtime=123)
    def listdir_iter(self, path, read_aheads):
        for name, mode in [('result 中文\'s.txt', stat.S_IFREG), ('link', stat.S_IFLNK), ('fifo', stat.S_IFIFO)]:
            yield SimpleNamespace(filename=name, st_mode=mode, st_size=12, st_mtime=123)


def test_paths_reject_symlink_special_escape_and_keep_unicode():
    from services.remote_files import safe_path, list_directory
    sftp = SFTP()
    assert safe_path(sftp, "/home/runner/result 中文's.txt", root='/home/runner', kind='file') == "/home/runner/result 中文's.txt"
    for path in ('/home/runner/link/child', '/home/runner/../outside', '/outside', '/home/runner/a\ncmd', 'relative'):
        with pytest.raises(HTTPException): safe_path(sftp, path, root='/home/runner')
    result = list_directory(sftp)
    assert result['cwd'] == '/home/runner'
    assert result['entries'][0]['path'] == "/home/runner/result 中文's.txt"
    assert result['entries'][1]['type'] == 'symlink'
    assert result['entries'][1]['disabled'] is True
    assert result['entries'][2]['disabled'] is True


def test_directory_cap():
    from services.remote_files import list_directory
    sftp = SFTP()
    sftp.listdir_iter = lambda *a, **k: (SimpleNamespace(filename=str(i), st_mode=stat.S_IFREG, st_size=0, st_mtime=0) for i in range(600))
    result = list_directory(sftp)
    assert len(result['entries']) == 500
    assert result['truncated'] is True


def test_proc_stat_parser_identity_and_no_gpu(tmp_path):
    from services.remote_probe import COLLECTOR_SCRIPT, parse_snapshot
    # Execute the same fixed program with /proc fixture adapters, not source assertions.
    files = {'/proc/self/mountinfo': '25 1 0:5 / /proc rw - proc proc rw\n', '/proc/sys/kernel/random/boot_id': 'boot-fixture', '/proc/meminfo': 'MemTotal: 1000 kB\nMemAvailable: 400 kB\n',
             '/proc/loadavg': '0.10 0.20 0.30 1/20 42', '/proc/stat': 'cpu 11 22 33 44 55 66 77 88 999 1000\n', '/sys/class/net/eth0/ifindex': '7\n', '/proc/net/dev': 'header\nheader\n eth0: 123 0 0 0 0 0 0 0 456 0 0 0 0 0 0 0\n',
             '/proc/42/stat': '42 (worker ) name) S 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 9876 20 21',
             '/proc/42/cmdline': b'python\x00train.py\x00', '/proc/42/status': 'VmRSS:\t20 kB\n'}
    import builtins, io, os, subprocess
    def fake_open(path, mode='r', *args, **kwargs):
        if str(path) not in files: raise FileNotFoundError(path)
        data = files[str(path)]
        return io.BytesIO(data) if 'b' in mode else io.StringIO(data)
    from unittest.mock import patch
    output = io.StringIO()
    with patch.object(builtins, 'open', fake_open), patch.object(os, 'scandir', side_effect=lambda path: __import__('contextlib').nullcontext(iter([SimpleNamespace(name='42')]))), patch.object(os, 'readlink', return_value='/home/runner'), patch.object(subprocess, 'run', side_effect=FileNotFoundError), patch('sys.stdout', output):
        exec(compile(COLLECTOR_SCRIPT, '<collector>', 'exec'), {'__name__': '__main__'})
    value = parse_snapshot(output.getvalue().encode())
    assert value['system']['gpu'] is None
    assert value['system']['disk'] is None and 'Disk usage unavailable' in value['warnings']
    assert value['system']['cpu']['percent'] is None
    assert value['processes'][0]['identity'] == 'boot-fixture:9876'
    assert value['processes'][0]['memory_bytes'] == 20480
    assert value['system']['memory']['total_bytes'] == 1024000
    assert value['system']['network'][0]['received_bytes'] == 123
    assert value['system']['network'][0]['ifindex'] == 7
    assert value['system']['cpu']['ticks'] == {'user':11,'nice':22,'system':33,'idle':44,'iowait':55,'irq':66,'softirq':77,'steal':88}
    assert value['sampling']['boot_id'] == 'boot-fixture'
    assert value['sampling']['monotonic_seconds'] >= 0
    files['/proc/42/stat'] = files['/proc/42/stat'].replace('9876', '9999')
    with patch.object(builtins, 'open', fake_open), patch.object(os, 'scandir', side_effect=lambda path: __import__('contextlib').nullcontext(iter([SimpleNamespace(name='42')]))), patch.object(os, 'readlink', return_value='/home/runner'), patch.object(subprocess, 'run', side_effect=FileNotFoundError), patch('sys.stdout', io.StringIO()) as output2:
        exec(compile(COLLECTOR_SCRIPT, '<collector>', 'exec'), {'__name__': '__main__'})
    assert parse_snapshot(output2.getvalue().encode())['processes'][0]['identity'] != value['processes'][0]['identity']


class Channel:
    def __init__(self, output=b'{}', stuck=False): self.output=output; self.stuck=stuck; self.closed=False
    def settimeout(self, value): pass
    def exec_command(self, command): pass
    def recv_ready(self): return bool(self.output)
    def recv_stderr_ready(self): return False
    def recv(self, n): result,self.output=self.output[:n],self.output[n:]; return result
    def exit_status_ready(self): return not self.stuck and not self.output
    def recv_exit_status(self): return 0
    def close(self): self.closed=True


def test_collector_output_and_time_bound(monkeypatch):
    from services import remote_probe as rp
    channel = Channel(b'x' * (1024*1024+1))
    client = SimpleNamespace(get_transport=lambda: SimpleNamespace(open_session=lambda **kw: channel))
    with pytest.raises(HTTPException) as exc: rp.collect_snapshot(client)
    assert exc.value.detail['code'] == 'collector_output_limit'
    assert channel.closed
    channel = Channel(stuck=True)
    ticks = iter([0, 21])
    monkeypatch.setattr(rp.time, 'monotonic', lambda: next(ticks))
    with pytest.raises(HTTPException) as exc: rp.collect_snapshot(client)
    assert exc.value.detail['code'] == 'collector_timeout'
    assert channel.closed


def test_malformed_snapshot_and_bounded_processes():
    from services.remote_probe import parse_snapshot
    for value in (b'not json', b'[]', b'{"processes": "bad"}'):
        with pytest.raises(HTTPException): parse_snapshot(value)
    payload = {'system': {'cpu':None,'memory':None,'network':None,'gpu':None}, 'processes': [{'pid':i+1, 'identity':f'boot:{i}', 'command':'x'*700} for i in range(220)], 'capabilities':{}, 'warnings':[]}
    result = parse_snapshot(json.dumps(payload).encode())
    assert len(result['processes']) == 200
    assert len(result['processes'][0]['command']) == 512


def test_tailscale_discovery_bounded_subprocess(monkeypatch):
    import subprocess, sys
    from services import remote_discovery as rd
    monkeypatch.setattr(rd.shutil, 'which', lambda name: '/fixture/tailscale')
    real_popen = subprocess.Popen
    def run_fixture(source):
        monkeypatch.setattr(rd.subprocess, 'Popen', lambda args, **kw: real_popen([sys.executable, '-c', source], **kw))
        return rd.discover_tailscale()
    result = run_fixture('import json; print(json.dumps({"BackendState":"Running","Peer":{"id":{"HostName":"lab","DNSName":"lab.example.","TailscaleIPs":["100.64.0.1"],"Online":True}}}))')
    assert result['available'] is True
    assert result['devices'] == [{'name':'lab','dns_name':'lab.example.','addresses':['100.64.0.1'],'online':True}]
    assert run_fixture('print("bad-json")')['available'] is False
    assert run_fixture('raise SystemExit(2)')['available'] is False
    assert run_fixture('print("x" * 600000)')['available'] is False


def test_target_pid_beyond_picker_cap_and_permission_unknown(monkeypatch):
    """Target is looked up directly even when discovery truncates or is denied."""
    import builtins, io, os, subprocess, sys
    from contextlib import nullcontext, redirect_stdout
    from services.remote_probe import COLLECTOR_SCRIPT, parse_snapshot
    blocked = set()
    def fake_open(path, mode='r', *args, **kwargs):
        path = str(path)
        if path in blocked: raise PermissionError()
        if path == '/proc/sys/kernel/random/boot_id': value = 'boot'
        elif path.endswith('/stat'): value = '9999 (train) S ' + ' '.join(['1']*18 + ['2345','1'])
        elif path.endswith('/cmdline'): return io.BytesIO(b'python\0train.py\0')
        elif path.endswith('/status'): value = 'VmRSS: 4 kB\n'
        else: raise FileNotFoundError()
        return io.StringIO(value)
    monkeypatch.setattr(builtins,'open',fake_open)
    monkeypatch.setattr(os,'scandir',lambda path: nullcontext(iter(SimpleNamespace(name=str(i)) for i in range(1,501))))
    monkeypatch.setattr(os,'readlink',lambda path:'/work')
    monkeypatch.setattr(subprocess,'run',lambda *a,**k: (_ for _ in ()).throw(FileNotFoundError()))
    def run(pid,query=None):
        monkeypatch.setattr(sys,'argv',['-c',json.dumps({'pid':pid,'query':query})])
        output = io.StringIO()
        with redirect_stdout(output): exec(compile(COLLECTOR_SCRIPT,'<collector>','exec'),{})
        return parse_snapshot(output.getvalue().encode())
    result = run(9999)
    assert len(result['processes']) == 200
    assert result['target_process']['process']['pid'] == 9999
    assert result['target_process']['status'] == 'present'
    blocked.add('/proc/9999/stat')
    assert run(9999)['target_process']['status'] == 'unavailable'
    assert run(None,'not-present')['processes'] == []


def test_process_state_survives_parser():
    from services.remote_probe import parse_snapshot
    payload = {'system':{'cpu':None,'memory':None,'network':None,'gpu':None},'processes':[{'pid':42,'identity':'boot:2','command':'train','state':'Z'}],'capabilities':{},'warnings':[]}
    assert parse_snapshot(json.dumps(payload).encode())['processes'][0]['state'] == 'Z'


def sampling_payload():
    return {'sampling':{'boot_id':'boot-fixture','monotonic_seconds':12.5},
            'system':{'cpu':{'percent':None,'load_average':[0.1,0.2,0.3],
                            'ticks':dict(zip(('user','nice','system','idle','iowait','irq','softirq','steal'),range(8)))},
                      'memory':{'total_bytes':1024,'available_bytes':512},
                      'network':[{'name':'eth0','received_bytes':123,'sent_bytes':456,'ifindex':7}], 'gpu':None},
            'processes':[{'pid':42,'identity':'boot:99','command':'train'}], 'capabilities':{},'warnings':[]}


def test_parser_additive_sampling_and_legacy_payload():
    from services.remote_probe import parse_snapshot
    payload = sampling_payload()
    parsed = parse_snapshot(json.dumps(payload))
    assert parsed['sampling'] == {'boot_id':'boot-fixture','monotonic_seconds':12.5}
    assert parsed['system']['cpu']['ticks']['steal'] == 7
    assert parsed['system']['network'][0]['ifindex'] == 7
    del payload['sampling']
    del payload['system']['cpu']['ticks']
    del payload['system']['network'][0]['ifindex']
    parsed = parse_snapshot(json.dumps(payload))
    assert parsed['sampling'] == {'boot_id':None,'monotonic_seconds':None}
    assert parsed['system']['cpu']['ticks'] is None
    assert parsed['system']['network'][0]['ifindex'] is None
    assert parsed['system']['cpu']['load_average'] == [0.1,0.2,0.3]
    assert parsed['processes'][0]['pid'] == 42
    assert parsed['warnings']


@pytest.mark.parametrize('bad',[True,-1,2**53,1.5,'3',None])
def test_parser_bad_ticks_and_network_values_degrade_without_losing_process(bad):
    from services.remote_probe import parse_snapshot
    payload = sampling_payload()
    payload['system']['cpu']['ticks']['iowait'] = bad
    payload['system']['network'][0].update(ifindex=bad,received_bytes=bad)
    parsed = parse_snapshot(json.dumps(payload))
    assert parsed['system']['cpu']['ticks'] is None
    assert parsed['system']['network'][0]['ifindex'] is None
    assert parsed['system']['network'][0]['received_bytes'] is None
    assert parsed['system']['network'][0]['sent_bytes'] == 456
    assert parsed['system']['memory']['available_bytes'] == 512
    assert parsed['processes'][0]['identity'] == 'boot:99'
    assert parsed['warnings']


@pytest.mark.parametrize('bad',[True,-0.1,float('nan'),float('inf'),'12.5',None,{}])
def test_parser_invalid_remote_clock_degrades(bad):
    from services.remote_probe import parse_snapshot
    payload = sampling_payload()
    payload['sampling']['monotonic_seconds'] = bad
    parsed = parse_snapshot(json.dumps(payload))
    assert parsed['sampling'] == {'boot_id':'boot-fixture','monotonic_seconds':None}
    assert parsed['warnings']


@pytest.mark.parametrize('bad',['','a'*129,'boot\nsecret',True,{},None])
def test_parser_invalid_boot_degrades(bad):
    from services.remote_probe import parse_snapshot
    payload = sampling_payload()
    payload['sampling']['boot_id'] = bad
    parsed = parse_snapshot(json.dumps(payload))
    assert parsed['sampling'] == {'boot_id':None,'monotonic_seconds':12.5}
    assert parsed['warnings']


def test_parser_safe_boundary_and_malformed_supplemental_shapes():
    from services.remote_probe import parse_snapshot
    payload = sampling_payload()
    payload['system']['cpu']['ticks']['user'] = 2**53-1
    payload['system']['network'][0].update(received_bytes=2**53-1,sent_bytes=0,ifindex=2**53-1)
    payload['sampling']['monotonic_seconds'] = 0
    parsed = parse_snapshot(json.dumps(payload))
    assert parsed['system']['cpu']['ticks']['user'] == 9007199254740991
    assert parsed['system']['network'][0] == {'name':'eth0','received_bytes':9007199254740991,'sent_bytes':0,'ifindex':9007199254740991}
    assert parsed['sampling']['monotonic_seconds'] == 0.0
    payload['sampling'] = ['invalid']
    payload['system']['cpu']['ticks'] = ['invalid']
    payload['system']['network'][0]['ifindex'] = 0
    parsed = parse_snapshot(json.dumps(payload))
    assert parsed['sampling'] == {'boot_id':None,'monotonic_seconds':None}
    assert parsed['system']['cpu']['ticks'] is None
    assert parsed['system']['network'][0]['ifindex'] is None
    assert parsed['processes'][0]['pid'] == 42


@pytest.mark.parametrize('cpu_bad', ['load','ticks'])
@pytest.mark.parametrize(('clock','boot','expected_clock','expected_boot'),[
    (123.5,'boot-fixture',123.5,'boot-fixture'),
    (float('nan'),'bad\nboot',None,None),
    (True,'a'*129,None,None),
    (-1,'boot-fixture',None,'boot-fixture'),
])
def test_collector_isolates_cpu_failure_and_bounds_interface_paths(monkeypatch,cpu_bad,clock,boot,expected_clock,expected_boot):
    import builtins, io, os, subprocess, sys, time
    from contextlib import nullcontext, redirect_stdout
    from services.remote_probe import COLLECTOR_SCRIPT, parse_snapshot
    files = {'/proc/loadavg':'bad' if cpu_bad == 'load' else '0.1 0.2 0.3',
             '/proc/stat':'cpu bad\n' if cpu_bad == 'ticks' else 'cpu 1 2 3 4 5 6 7 8 99 100\n',
             '/proc/meminfo':'MemTotal: 100 kB\nMemAvailable: 40 kB\n',
             '/proc/sys/kernel/random/boot_id':boot,
             '/proc/net/dev':'eth0: 123 0 0 0 0 0 0 0 456\n../escape: 1 0 0 0 0 0 0 0 2\n',
             '/sys/class/net/eth0/ifindex':'9007199254740992\n'}
    paths = []
    def fake_open(path,*args,**kwargs):
        paths.append(path)
        if path not in files: raise FileNotFoundError()
        return io.StringIO(files[path])
    monkeypatch.setattr(builtins,'open',fake_open)
    monkeypatch.setattr(os,'scandir',lambda path:nullcontext(iter([])))
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:(_ for _ in ()).throw(FileNotFoundError()))
    monkeypatch.setattr(sys,'argv',['-c'])
    monkeypatch.setattr(time,'monotonic',lambda:clock)
    output = io.StringIO()
    with redirect_stdout(output): exec(compile(COLLECTOR_SCRIPT,'<collector>','exec'),{})
    raw = json.loads(output.getvalue())
    assert raw['sampling'] == {'boot_id':expected_boot,'monotonic_seconds':expected_clock}
    assert raw['system']['network'][0]['ifindex'] is None
    parsed = parse_snapshot(output.getvalue())
    assert parsed['sampling'] == {'boot_id':expected_boot,'monotonic_seconds':expected_clock}
    assert parsed['system']['memory']['available_bytes'] == 40960
    assert parsed['system']['cpu']['load_average'] == (None if cpu_bad == 'load' else [0.1,0.2,0.3])
    assert parsed['system']['cpu']['ticks'] == (None if cpu_bad == 'ticks' else {'user':1,'nice':2,'system':3,'idle':4,'iowait':5,'irq':6,'softirq':7,'steal':8})
    assert parsed['system']['network'][0]['ifindex'] is None
    assert all('/../' not in path for path in paths)
    assert parsed['warnings']


@pytest.mark.parametrize(('mountinfo','expected'),[
    ('25 1 0:5 / /proc rw,nosuid,nodev,noexec - proc proc rw\n','missing'),
    ('25 1 0:5 / /proc rw - proc proc rw,hidepid=0\n','missing'),
    ('25 1 0:5 / /proc rw - proc proc rw,hidepid=2\n','unavailable'),
    ('25 1 0:5 / /proc rw - proc proc rw,hidepid=invisible\n','unavailable'),
    (None,'unavailable'),
    ('malformed mountinfo\n','unavailable'),
])
def test_concealed_pid_enoent_does_not_prove_exit(monkeypatch,mountinfo,expected):
    import builtins, errno, io, os, subprocess, sys
    from contextlib import nullcontext, redirect_stdout
    from services.remote_probe import COLLECTOR_SCRIPT, parse_snapshot
    def fake_open(path,mode='r',*args,**kwargs):
        if path == '/proc/sys/kernel/random/boot_id': return io.StringIO('boot')
        if path == '/proc/self/mountinfo':
            if mountinfo is None: raise PermissionError(errno.EACCES,'denied')
            return io.StringIO(mountinfo)
        raise FileNotFoundError(errno.ENOENT,'concealed or missing')
    monkeypatch.setattr(builtins,'open',fake_open)
    monkeypatch.setattr(os,'scandir',lambda path:nullcontext(iter([])))
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:(_ for _ in ()).throw(FileNotFoundError()))
    monkeypatch.setattr(sys,'argv',['-c',json.dumps({'pid':9999})])
    output = io.StringIO()
    with redirect_stdout(output): exec(compile(COLLECTOR_SCRIPT,'<collector>','exec'),{})
    result = parse_snapshot(output.getvalue().encode())
    assert result['target_process']['status'] == expected
    assert result['target_process']['process'] is None
    from services import remote_connection, remote_probe, workloads
    monkeypatch.setattr(remote_connection, 'open_remote', lambda *args: nullcontext(object()))
    monkeypatch.setattr(remote_probe, 'collect_snapshot', lambda *args, **kwargs: result)
    monkeypatch.setattr(remote_connection, 'owned_server', lambda *args: SimpleNamespace(id=71,host='fixture.invalid',port=22,username='lab'))
    workload = SimpleNamespace(id=1, server_id=71, root_path='/work', pid=9999, process_identity='boot:2345', log_path=None)
    observed = workloads.observation(None, None, workload)
    assert observed['process_status'] == ('exited_or_missing' if expected == 'missing' else 'unknown')


def test_collector_reports_real_filesystems_and_parser_bounds_disk():
    import builtins, io, os, subprocess
    from unittest.mock import patch
    from services.remote_probe import COLLECTOR_SCRIPT, parse_snapshot
    files = {'/proc/self/mountinfo': '25 1 0:5 / /proc rw - proc proc rw\n',
             '/proc/self/mounts': ('/dev/sda1 / ext4 rw 0 0\nproc /proc proc rw 0 0\ntmpfs /run tmpfs rw 0 0\n'
                                   '/dev/sdb1 /data\\040set xfs rw 0 0\n/dev/sda1 /var/lib/docker ext4 rw 0 0\n'
                                   '/dev/loop0 /snap/core squashfs ro 0 0\n/dev/sdc1 /gone ext4 rw 0 0\n')}
    def fake_open(path, mode='r', *args, **kwargs):
        if str(path) not in files: raise FileNotFoundError(path)
        return io.StringIO(files[str(path)])
    sizes = {'/': (4096, 1000, 400, 300), '/data set': (4096, 2000, 1000, 900)}
    def statvfs(path):
        if path not in sizes: raise PermissionError(path)
        frsize, blocks, bfree, bavail = sizes[path]
        return os.statvfs_result((frsize, frsize, blocks, bfree, bavail, 0, 0, 0, 0, 255))
    output = io.StringIO()
    with patch.object(builtins, 'open', fake_open), patch.object(os, 'statvfs', statvfs), \
         patch.object(os, 'scandir', side_effect=lambda path: __import__('contextlib').nullcontext(iter([]))), \
         patch.object(subprocess, 'run', side_effect=FileNotFoundError), patch('sys.stdout', output):
        exec(compile(COLLECTOR_SCRIPT, '<collector>', 'exec'), {'__name__': '__main__'})
    disk = parse_snapshot(output.getvalue().encode())['system']['disk']
    assert disk == [
        {'mountpoint': '/', 'total_bytes': 4096000, 'available_bytes': 1228800, 'used_bytes': 2457600},
        {'mountpoint': '/data set', 'total_bytes': 8192000, 'available_bytes': 3686400, 'used_bytes': 4096000},
    ]
    payload = {'system': {'cpu': None, 'memory': None, 'network': None, 'gpu': None,
                          'disk': [{'mountpoint': '/ok', 'total_bytes': 10, 'available_bytes': 5, 'used_bytes': 5},
                                   {'mountpoint': '/bad', 'total_bytes': -1, 'available_bytes': 5, 'used_bytes': 5},
                                   {'mountpoint': 7, 'total_bytes': 10, 'available_bytes': 5, 'used_bytes': 5}] + [
                                   {'mountpoint': f'/m{i}', 'total_bytes': 1, 'available_bytes': 0, 'used_bytes': 1} for i in range(40)]},
               'processes': [], 'capabilities': {}, 'warnings': []}
    parsed = parse_snapshot(json.dumps(payload).encode())['system']['disk']
    assert parsed[0]['mountpoint'] == '/ok' and len(parsed) == 32
    assert all(d['mountpoint'] != '/bad' for d in parsed)
    legacy = {'system': {'cpu': None, 'memory': None, 'network': None, 'gpu': None}, 'processes': [], 'capabilities': {}, 'warnings': []}
    assert parse_snapshot(json.dumps(legacy).encode())['system'].get('disk') is None
