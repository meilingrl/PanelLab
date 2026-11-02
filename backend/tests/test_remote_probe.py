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
    files = {'/proc/sys/kernel/random/boot_id': 'boot-fixture', '/proc/meminfo': 'MemTotal: 1000 kB\nMemAvailable: 400 kB\n',
             '/proc/loadavg': '0.10 0.20 0.30 1/20 42', '/proc/net/dev': 'header\nheader\n eth0: 123 0 0 0 0 0 0 0 456 0 0 0 0 0 0 0\n',
             '/proc/42/stat': '42 (worker ) name) S 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 9876 20 21',
             '/proc/42/cmdline': b'python\x00train.py\x00', '/proc/42/status': 'VmRSS:\t20 kB\n'}
    import builtins, io, os, subprocess
    def fake_open(path, mode='r', *args, **kwargs):
        data = files[str(path)]
        return io.BytesIO(data) if 'b' in mode else io.StringIO(data)
    from unittest.mock import patch
    output = io.StringIO()
    with patch.object(builtins, 'open', fake_open), patch.object(os, 'scandir', side_effect=lambda path: __import__('contextlib').nullcontext(iter([SimpleNamespace(name='42')]))), patch.object(os, 'readlink', return_value='/home/runner'), patch.object(subprocess, 'run', side_effect=FileNotFoundError), patch('sys.stdout', output):
        exec(compile(COLLECTOR_SCRIPT, '<collector>', 'exec'), {'__name__': '__main__'})
    value = parse_snapshot(output.getvalue().encode())
    assert value['system']['gpu'] is None
    assert value['system']['cpu']['percent'] is None
    assert value['processes'][0]['identity'] == 'boot-fixture:9876'
    assert value['processes'][0]['memory_bytes'] == 20480
    assert value['system']['memory']['total_bytes'] == 1024000
    assert value['system']['network'][0]['received_bytes'] == 123
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
