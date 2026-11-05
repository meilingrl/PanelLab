"""Onboarding modes exercise the entry as a stdlib-only subprocess."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
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
        def do_GET(self):
            state['requests'].append((self.command, self.path, self.headers.get('Authorization')))
            self.send_response(state['status'])
            self.end_headers()
            self.wfile.write(json.dumps(state['payload']).encode())
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
