"""Read-only shell history: bounded tail read, parsing, dedupe and secret filtering."""
import io
import stat
from contextlib import nullcontext
from types import SimpleNamespace

import pytest


class HistorySFTP:
    """Fake SFTP with a home directory and optional history files."""

    def __init__(self, files, home='/home/admin', links=()):
        self.files = {home + '/' + name: data for name, data in files.items()}
        self.home = home
        self.links = {home + '/' + name for name in links}

    def normalize(self, path):
        return self.home if path == '.' else path

    def lstat(self, path):
        if path in self.links:
            return SimpleNamespace(st_mode=stat.S_IFLNK, st_size=0)
        if path in self.files:
            return SimpleNamespace(st_mode=stat.S_IFREG, st_size=len(self.files[path]))
        if path == '/' or self.home.startswith(path):
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_size=0)
        raise FileNotFoundError(2, 'missing')

    def open(self, path, mode='r'):
        assert mode == 'rb'
        return io.BytesIO(self.files[path])


def read(files, **kw):
    from services.shell_history import read_shell_history
    return read_shell_history(HistorySFTP(files, **kw))


def test_bash_history_newest_first_and_deduped():
    data = b'ls -la\ncd /srv\nls -la\nsudo systemctl status nginx\n'
    result = read({'.bash_history': data})
    assert result['commands'] == ['sudo systemctl status nginx', 'ls -la', 'cd /srv']
    assert result['sources'] == ['.bash_history']
    assert result['truncated'] is False


def test_bash_timestamps_and_zsh_extended_format():
    bash = b'#1731600000\nuptime\n#1731600001\ndf -h\n'
    zsh = b': 1731600002:0;git status\n: 1731600003:0;htop\n'
    result = read({'.bash_history': bash, '.zsh_history': zsh})
    assert set(result['commands']) == {'uptime', 'df -h', 'git status', 'htop'}
    assert result['sources'] == ['.bash_history', '.zsh_history']


def test_missing_files_give_empty_list():
    result = read({})
    assert result == {'commands': [], 'sources': [], 'truncated': False}


def test_symlinked_history_is_not_followed():
    result = read({'.zsh_history': b'whoami\n'}, links=('.bash_history',))
    assert result['commands'] == ['whoami']
    assert result['sources'] == ['.zsh_history']


def test_lines_that_look_like_secrets_are_dropped():
    data = (b'mysql -u root -pS3cret db\nexport API_KEY=abc\ncurl -H "Authorization: Bearer x" u\n'
            b'echo token=zzz\nPASSWORD=1 ./run\nsshpass -p hunter2 ssh h\nls\n')
    result = read({'.bash_history': data})
    assert result['commands'] == ['ls']


def test_control_characters_and_long_lines_dropped():
    data = b'ok\nbad\x1b[31m\n' + b'x' * 600 + b'\n   \n'
    assert read({'.bash_history': data})['commands'] == ['ok']


def test_only_the_tail_of_a_large_file_is_read(monkeypatch):
    from services import shell_history
    monkeypatch.setattr(shell_history, 'MAX_BYTES', 64)
    lines = b''.join(b'cmd%03d\n' % i for i in range(100))
    result = read({'.bash_history': lines})
    assert result['truncated'] is True
    assert result['commands'][0] == 'cmd099'
    assert all(c.startswith('cmd') and len(c) == 6 for c in result['commands'])


def test_at_most_max_commands(monkeypatch):
    from services import shell_history
    monkeypatch.setattr(shell_history, 'MAX_COMMANDS', 3)
    data = b''.join(b'c%d\n' % i for i in range(10))
    result = read({'.bash_history': data})
    assert result['commands'] == ['c9', 'c8', 'c7']
    assert result['truncated'] is True


def test_invalid_utf8_is_replaced_not_fatal():
    result = read({'.bash_history': b'echo \xff\nls\n'})
    assert result['commands'][0] == 'ls'
    assert result['commands'][1].startswith('echo ')


def test_endpoint_uses_owned_pinned_connection(monkeypatch):
    from routers import remote
    from services import remote_connection, remote_files
    seen = {}

    def fake_open_remote(db, user, server_id):
        seen['server_id'] = server_id
        return nullcontext(object())

    monkeypatch.setattr(remote_connection, 'open_remote', fake_open_remote)
    monkeypatch.setattr(remote_files, 'open_sftp', lambda client: nullcontext(HistorySFTP({'.bash_history': b'pwd\n'})))
    result = remote.shell_history(7, None, None)
    assert seen['server_id'] == 7
    assert result['commands'] == ['pwd']
    assert 'observed_at' in result


@pytest.mark.parametrize('line', [
    'docker login -u me -p hunter2', 'curl -u me:pw https://x', 'git clone https://me:ghp_x@github.com/a/b',
    'export DB_PASS=abc', 'MYSQL_PWD=abc mysql', 'redis-cli -a pw', 'echo ghp_abcdefghijklmnop',
    'echo sk-abcdefghijklmnopqrst', 'aws AKIAABCDEFGHIJKLMNOP', 'echo pw | sudo -S ls', 'smbclient //h/s -U me%pw',
    'mysql -u root -pS3cret db',
])
def test_more_secret_shapes_are_dropped(line):
    assert read({'.bash_history': (line + '\n').encode()})['commands'] == []


@pytest.mark.parametrize('line', [
    'netstat -plnt', 'ss -plnt', 'find . -path x -print', 'cp -pr a b', 'mkdir -pv d', 'grep -Pi x f',
    'rsync -Pav a b', 'docker run -p8080:80 img', 'ssh -p 22 host', 'git push', 'mkdir -p dir',
    'sudo -s', 'docker run -u 1000:1000 img', 'docker exec -u root:root c sh',
])
def test_common_commands_are_kept(line):
    assert read({'.bash_history': (line + '\n').encode()})['commands'] == [line]


def test_zsh_metafied_chinese_is_restored():
    text = 'echo 你好'.encode()
    meta = bytearray()
    for b in text:
        if 0x83 <= b <= 0x9f:
            meta += bytes([0x83, b ^ 0x20])
        else:
            meta.append(b)
    result = read({'.zsh_history': b': 1:0;' + bytes(meta) + b'\n'})
    assert result['commands'] == ['echo 你好']
