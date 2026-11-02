"""Real temporary files and child processes exercise recovery."""
import shlex
import sys
from dataclasses import replace
import pytest
from services.nginx_manager import NginxManager, NginxManagerError, SiteRuntimeConfig
SITE = SiteRuntimeConfig(1, 'web', 'web.example.com', 'static', '/var/www/web', None, 8080, True, 'web.conf')
@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setenv('NGINX_CONF_DIR', str(tmp_path / 'conf'))
    monkeypatch.setenv('NGINX_SKIP_APPLY', 'false')
    return NginxManager()

def script(manager, tmp_path, mode):
    p = tmp_path / 'controlled.py'
    p.write_text('''import sys, pathlib, time
root = pathlib.Path(sys.argv[1]); mode = sys.argv[2]; action = sys.argv[3]
conf = root / 'conf' / 'web.conf'
with (root / 'observed').open('a') as f: f.write(action + ':' + (conf.read_text() if conf.exists() else '<missing>') + '\\n')
if mode == 'timeout': time.sleep(3)
if mode == 'test_fail' and action == 'test': sys.exit(1)
if mode == 'reload_fail' and action == 'reload' and conf.read_text() != 'original': sys.exit(1)
if mode == 'recovery_fail' and action == 'reload': sys.exit(1)
''')
    cmd = f'{shlex.quote(sys.executable)} {shlex.quote(str(p))} {shlex.quote(str(tmp_path))} {mode}'
    manager.settings.nginx_test_cmd = cmd + ' test'; manager.settings.nginx_reload_cmd = cmd + ' reload'

@pytest.mark.parametrize('field,value', [('domain', 'ok; include /secret'), ('domain', 'a\nb'), ('root_path', '/tmp/$host'), ('root_path', '/tmp/a b'), ('proxy_target', 'http://user:secret@localhost'), ('proxy_target', 'http://localhost/a?token=x'), ('proxy_target', 'http://localhost/a#b'), ('proxy_target', 'http://localhost/$host')])
def test_render_rejects_instruction_and_secret_inputs(manager, field, value):
    site = replace(SITE, **{field: value})
    if field == 'proxy_target': site = replace(site, site_type='proxy', root_path=None)
    with pytest.raises(NginxManagerError): manager.render_site_config(site)

@pytest.mark.parametrize('mode,status', [('ok', 'applied'), ('test_fail', 'failed_restored'), ('reload_fail', 'failed_restored'), ('recovery_fail', 'recovery_failed'), ('timeout', 'failed_restored')])
def test_atomic_change_and_recovery(manager, tmp_path, mode, status):
    conf = tmp_path / 'conf'; conf.mkdir(); target = conf / 'web.conf'; target.write_text('original')
    script(manager, tmp_path, mode); manager.settings.nginx_cmd_timeout_seconds = 1
    result = manager.apply_change('web.conf', 'candidate')
    assert result.status == status
    assert target.read_text() == ('candidate' if mode == 'ok' else 'original')
    assert not list(conf.glob('*.tmp'))
    log = (tmp_path / 'observed').read_text(); assert 'test:candidate' in log
    if mode == 'reload_fail': assert 'test:original\nreload:original' in log

def test_snapshot_no_creation_and_symlink_rejection(manager, tmp_path):
    first = manager.fingerprint(); assert not (tmp_path / 'conf').exists()
    (tmp_path / 'conf').mkdir(); assert first != manager.fingerprint()
    outside = tmp_path / 'secret'; outside.write_text('private')
    (tmp_path / 'conf' / 'evil.conf').symlink_to(outside)
    with pytest.raises(NginxManagerError): manager.fingerprint()
    with pytest.raises(NginxManagerError): manager.apply_change('../secret', 'bad')
    assert outside.read_text() == 'private'

def test_delete_failure_restores_bytes(manager, tmp_path):
    (tmp_path / 'conf').mkdir(); target = tmp_path / 'conf/web.conf'; target.write_text('original')
    script(manager, tmp_path, 'test_fail')
    assert manager.apply_change('web.conf', None).status == 'failed_restored'
    assert target.read_bytes() == b'original'
