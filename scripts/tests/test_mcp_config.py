"""Onboarding modes exercise the entry as a stdlib-only subprocess."""
import getpass
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
ENTRY = SCRIPTS / 'panellab_mcp.py'
SECRET = 'PRIVATE-env-credential'


def run_entry(*args, cwd, entry=ENTRY):
    return subprocess.run([sys.executable, '-S', str(entry), *args], cwd=cwd,
                          env={**os.environ, 'PANEL_TOKEN': SECRET},
                          capture_output=True, text=True, timeout=5)


@pytest.fixture
def api():
    state = {'requests': [], 'status': 200, 'payload': {'items': [
        {'id': 2, 'name': 'PRIVATE-server', 'host': 'PRIVATE-host', 'port': 22,
         'username': 'PRIVATE-user', 'password': 'PRIVATE-password'}]}}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def reply(self, status, payload):
            self.send_response(status)
            if 300 <= status < 400:
                self.send_header('Location', '/PRIVATE-redirect')
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            state['requests'].append((self.command, self.path, self.headers.get('Authorization'), body))
            self.reply(state.get('login_status', 200), state.get('login_payload', {'token': 'PRIVATE-new-token'}))
        def do_GET(self):
            state['requests'].append((self.command, self.path, self.headers.get('Authorization')))
            self.reply(state['status'], state['payload'])
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}', state
    server.shutdown()
    server.server_close()
    thread.join()


def test_config_has_absolute_paths_and_does_not_read_file_or_contact_api(tmp_path, api):
    url, state = api
    # A FIFO would block if the generator attempted to read its credential.
    token = tmp_path / '凭据 空格'
    os.mkfifo(token)
    copied_scripts = tmp_path / '工具 空格'
    copied_scripts.mkdir()
    for filename in ('panellab.py', 'panellab_mcp.py'):
        shutil.copy2(SCRIPTS / filename, copied_scripts / filename)
    entry = copied_scripts / 'panellab_mcp.py'
    result = run_entry('--print-config', '--token-file', token.name, '--url', url + '/',
                       cwd=tmp_path, entry=entry)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ''
    assert json.loads(result.stdout) == {'mcpServers': {'panellab': {
        'command': str(Path(sys.executable).absolute()),
        'args': [str(entry), '--url', url, '--token-file', str(token)]}}}
    assert SECRET not in result.stdout and 'env' not in json.loads(result.stdout)['mcpServers']['panellab']
    assert not state['requests']
    assert sorted(p.name for p in tmp_path.iterdir()) == ['凭据 空格', '工具 空格']


def test_config_accepts_nonexistent_token_path_and_preserves_explicit_insecure(tmp_path):
    result = run_entry('--print-config', '--token-file', 'missing 中文/token',
                       '--url', 'http://panel.example', '--allow-insecure', cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['mcpServers']['panellab']['args'] == [
        str(ENTRY), '--url', 'http://panel.example', '--token-file',
        str(tmp_path / 'missing 中文/token'), '--allow-insecure']


@pytest.mark.parametrize('args', [
    ['--print-config'], ['--print-config', '--token-file', ''],
    ['--print-config', '--check', '--token-file', 'missing'],
    ['--print-config', '--token-file', 'missing', '--token', SECRET],
    ['--print-config', '--token-file', 'missing', '--url', 'http://user:PRIVATE@example.com'],
    ['--print-config', '--token-file', 'missing', '--url', 'https://example.com?token=PRIVATE'],
    ['--print-config', '--token-file', 'missing', '--url', 'https://example.com/#PRIVATE'],
    ['--print-config', '--token-file', 'missing', '--url', 'ftp://PRIVATE'],
    ['--print-config', '--token-file', 'missing', '--url', 'http://panel.example'],
    ['--check', '--url', 'http://user:PRIVATE@example.com'],
])
def test_invalid_arguments_are_redacted(tmp_path, args):
    result = run_entry(*args, cwd=tmp_path)
    assert result.returncode != 0 and result.stdout == '' and result.stderr
    assert 'PRIVATE' not in result.stderr and 'Traceback' not in result.stderr


def test_check_uses_token_file_and_returns_only_count_without_sdk(tmp_path, api):
    url, state = api
    token = tmp_path / '凭据 空格'
    token.write_text('file-credential', encoding='utf-8')
    result = run_entry('--check', '--url', url, '--token-file', str(token), cwd=tmp_path)
    assert result.returncode == 0 and result.stderr == ''
    assert json.loads(result.stdout) == {'ok': True, 'server_count': 1}
    assert state['requests'] == [('GET', '/api/servers', 'Bearer file-credential')]
    assert 'PRIVATE' not in result.stdout and 'file-credential' not in result.stdout


def test_check_empty_account_is_success(tmp_path, api):
    url, state = api
    state['payload'] = {'items': []}
    result = run_entry('--check', '--url', url, cwd=tmp_path)
    assert result.returncode == 0 and json.loads(result.stdout) == {'ok': True, 'server_count': 0}
    assert state['requests'] == [('GET', '/api/servers', 'Bearer ' + SECRET)]


@pytest.mark.parametrize('status,payload', [
    (401, {'detail': 'PRIVATE-auth ' + SECRET}),
    (500, {'detail': 'PRIVATE-failure ' + SECRET}),
    (200, {'items': 'PRIVATE'}), (200, {'items': [None]}), (200, []),
])
def test_check_failure_is_nonzero_and_never_prints_response(tmp_path, api, status, payload):
    url, state = api
    state.update(status=status, payload=payload)
    result = run_entry('--check', '--url', url, cwd=tmp_path)
    assert result.returncode != 0 and result.stdout == '' and result.stderr
    assert 'PRIVATE' not in result.stderr and 'Traceback' not in result.stderr
    assert len(state['requests']) == 1


@pytest.mark.parametrize('status,hint', [
    (401, 'Sign in again'), (403, 'account access'),
    (404, 'API URL'), (409, 'API configuration'),
])
def test_check_http_reports_safe_status_and_recovery_hint(tmp_path, api, status, hint):
    url, state = api
    state.update(status=status, payload={'detail': 'PRIVATE ' + SECRET})
    result = run_entry('--check', '--url', url, cwd=tmp_path)
    assert result.returncode != 0 and result.stdout == ''
    assert f'HTTP {status}' in result.stderr and hint in result.stderr
    assert 'PRIVATE' not in result.stderr and 'Traceback' not in result.stderr


def test_unexpected_check_exception_is_sanitized(capsys, monkeypatch):
    spec = importlib.util.spec_from_file_location('panellab_mcp_config_test', ENTRY)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.syspath_prepend(str(SCRIPTS))
    spec.loader.exec_module(module)
    requests = []
    class BrokenAPI:
        def request(self, *args):
            requests.append(args)
            raise RuntimeError('PRIVATE ' + SECRET)
    monkeypatch.setattr(module, 'configured_api', lambda args: BrokenAPI())
    assert module.main(['--check']) != 0
    output = capsys.readouterr()
    assert output.out == '' and output.err
    assert 'PRIVATE' not in output.err and 'Traceback' not in output.err
    assert requests == [('GET', '/api/servers')]


def test_help_and_missing_sdk_serve_keep_stdio_clean(tmp_path):
    help_result = run_entry('--help', cwd=tmp_path)
    assert help_result.returncode == 0 and help_result.stderr == ''
    assert '--print-config' in help_result.stdout and '--check' in help_result.stdout
    missing_sdk = run_entry(cwd=tmp_path)
    assert missing_sdk.returncode != 0 and missing_sdk.stdout == ''
    assert 'requirements-mcp.txt' in missing_sdk.stderr and SECRET not in missing_sdk.stderr


@pytest.fixture
def setup_entry(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    import panellab_mcp
    credentials = iter(('PRIVATE-user', 'PRIVATE-pass'))
    monkeypatch.setattr(getpass, 'getpass', lambda prompt: next(credentials))
    monkeypatch.setenv('PANEL_TOKEN', SECRET)
    return panellab_mcp.main


def test_setup_logs_in_checks_and_prints_existing_config(tmp_path, api, setup_entry, capsys):
    url, state = api
    target = tmp_path / '新 目录' / '面板.token'
    assert setup_entry(['--setup', '--url', url, '--token-file', str(target)]) == 0
    output = capsys.readouterr()
    assert 'PRIVATE' not in output.out + output.err
    assert json.loads(output.out) == {'mcpServers': {'panellab': {
        'command': str(Path(sys.executable).absolute()),
        'args': [str(ENTRY), '--url', url, '--token-file', str(target)]}}}
    assert target.read_text() == 'PRIVATE-new-token\n'
    assert target.stat().st_mode & 0o777 == 0o600
    assert target.parent.stat().st_mode & 0o777 == 0o700
    assert list(target.parent.iterdir()) == [target]
    assert state['requests'] == [
        ('POST', '/api/auth/login', None, {'username': 'PRIVATE-user', 'password': 'PRIVATE-pass'}),
        ('GET', '/api/servers', 'Bearer PRIVATE-new-token')]


@pytest.mark.parametrize('changes,paths', [
    ({'login_status': 302}, ['/api/auth/login']),
    ({'login_status': 401}, ['/api/auth/login']),
    ({'login_payload': {'token': 'PRIVATE\x7f'}}, ['/api/auth/login']),
    ({'login_payload': {'token': 'PRIVATE\nInjected'}}, ['/api/auth/login']),
    ({'login_payload': {'token': 'x' * (1024 * 1024)}}, ['/api/auth/login']),
    ({'status': 307}, ['/api/auth/login', '/api/servers']),
    ({'status': 403}, ['/api/auth/login', '/api/servers']),
    ({'payload': {}}, ['/api/auth/login', '/api/servers']),
])
def test_setup_api_failure_leaves_no_file_or_output(tmp_path, api, setup_entry, capsys, changes, paths):
    url, state = api
    state.update(changes)
    assert setup_entry(['--setup', '--url', url, '--token-file', str(tmp_path / 'token')]) != 0
    output = capsys.readouterr()
    assert output.out == '' and output.err and 'PRIVATE' not in output.err
    assert list(tmp_path.iterdir()) == []
    assert [req[1] for req in state['requests']] == paths


@pytest.mark.parametrize('existing', ['file', 'dangling_symlink'])
def test_setup_refuses_existing_target_before_login(tmp_path, api, setup_entry, capsys, existing):
    url, state = api
    target = tmp_path / 'token'
    if existing == 'file':
        target.write_text('old-token')
    else:
        target.symlink_to(tmp_path / 'missing')
    assert setup_entry(['--setup', '--url', url, '--token-file', str(target)]) != 0
    assert capsys.readouterr().out == '' and not state['requests']
    if existing == 'file':
        assert target.read_text() == 'old-token'
    else:
        assert target.is_symlink() and not target.exists()


def test_setup_never_falls_back_to_echoed_input(tmp_path, api, setup_entry, monkeypatch, capsys):
    url, state = api
    def fallback(prompt):
        warnings.warn('PRIVATE fallback', getpass.GetPassWarning)
        pytest.fail('getpass reached its echoed-input fallback')
    monkeypatch.setattr(getpass, 'getpass', fallback)
    assert setup_entry(['--setup', '--url', url, '--token-file', str(tmp_path / 'token')]) != 0
    output = capsys.readouterr()
    assert output.out == '' and 'terminal' in output.err and 'PRIVATE' not in output.err
    assert not state['requests'] and list(tmp_path.iterdir()) == []


def test_setup_never_overwrites_file_created_during_login(tmp_path, api, setup_entry, monkeypatch, capsys):
    url, state = api
    target = tmp_path / 'token'
    def concurrent_create(prompt):
        target.touch(exist_ok=True)
        target.write_text('other credential')
        return 'PRIVATE'
    monkeypatch.setattr(getpass, 'getpass', concurrent_create)
    assert setup_entry(['--setup', '--url', url, '--token-file', str(target)]) != 0
    output = capsys.readouterr()
    assert output.out == '' and 'PRIVATE' not in output.err
    assert target.read_text() == 'other credential' and list(tmp_path.iterdir()) == [target]
    assert [req[1] for req in state['requests']] == ['/api/auth/login', '/api/servers']


@pytest.mark.parametrize('args', [
    ['--setup'], ['--setup', '--check'], ['--setup', '--print-config'],
    ['--setup', '--token-file', 'unused', '--url', 'http://example.org', '--allow-insecure'],
])
def test_setup_invalid_arguments_without_sdk(tmp_path, args):
    result = run_entry(*args, cwd=tmp_path)
    assert result.returncode != 0 and result.stdout == '' and result.stderr
    assert 'PRIVATE' not in result.stderr and not list(tmp_path.iterdir())


def test_setup_disk_failure_discards_unpublished_token(tmp_path, api, setup_entry, monkeypatch, capsys):
    url, state = api
    def disk_full(_fd):
        raise OSError('PRIVATE disk error')
    monkeypatch.setattr(os, 'fsync', disk_full)
    assert setup_entry(['--setup', '--url', url, '--token-file', str(tmp_path / 'token')]) != 0
    output = capsys.readouterr()
    assert output.out == '' and output.err and 'PRIVATE' not in output.err
    assert [req[1] for req in state['requests']] == ['/api/auth/login', '/api/servers']
    assert list(tmp_path.iterdir()) == []


def test_setup_rejects_shared_writable_directory_before_login(tmp_path, api, setup_entry, capsys):
    url, state = api
    directory = tmp_path / 'shared'
    directory.mkdir()
    directory.chmod(0o770)
    assert setup_entry(['--setup', '--url', url, '--token-file', str(directory / 'token')]) != 0
    output = capsys.readouterr()
    assert output.out == '' and 'directory' in output.err
    assert not state['requests'] and list(directory.iterdir()) == []


def test_setup_loopback_ignores_http_proxy(tmp_path, api, setup_entry, monkeypatch, capsys):
    url, state = api
    monkeypatch.setenv('http_proxy', 'http://127.0.0.1:1')
    monkeypatch.setenv('no_proxy', 'unrelated.invalid')
    assert setup_entry(['--setup', '--url', url, '--token-file', str(tmp_path / 'token')]) == 0
    assert [req[1] for req in state['requests']] == ['/api/auth/login', '/api/servers']
    assert 'PRIVATE' not in capsys.readouterr().out


def test_check_for_setup_config_keeps_loopback_bearer_off_proxies(tmp_path, api, monkeypatch):
    url, state = api
    monkeypatch.setenv('http_proxy', 'http://127.0.0.1:1')
    monkeypatch.setenv('no_proxy', 'unrelated.invalid')
    target = tmp_path / 'token'
    target.write_text('new-token')
    result = run_entry('--check', '--url', url, '--token-file', str(target), cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert state['requests'] == [('GET', '/api/servers', 'Bearer new-token')]


def test_setup_creates_private_intermediate_directories_even_with_open_umask(tmp_path, api, setup_entry, capsys):
    url, _ = api
    target = tmp_path / 'outer' / 'inner' / 'token'
    previous = os.umask(0)
    try:
        assert setup_entry(['--setup', '--url', url, '--token-file', str(target)]) == 0
    finally:
        os.umask(previous)
    assert target.parent.parent.stat().st_mode & 0o777 == 0o700
    assert target.parent.stat().st_mode & 0o777 == 0o700


def test_setup_rejects_private_dir_replaceable_by_others(tmp_path, api, setup_entry, capsys):
    url, state = api
    shared = tmp_path / 'shared'
    private = shared / 'private'
    private.mkdir(parents=True, mode=0o700)
    shared.chmod(0o777)
    assert setup_entry(['--setup', '--url', url, '--token-file', str(private / 'token')]) != 0
    assert not state['requests'] and list(private.iterdir()) == []
    assert capsys.readouterr().out == ''
