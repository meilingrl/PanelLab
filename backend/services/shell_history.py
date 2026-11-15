"""Read-only view of the login user's shell history over pinned SFTP.

Only the tail of ~/.bash_history and ~/.zsh_history is read (symlinks are not
followed). Lines that look like they carry secrets, contain control characters
or are very long are dropped, so the list can be shown on screen safely.
"""
import posixpath
import re

from fastapi import HTTPException

from services.remote_files import safe_path

HISTORY_FILES = ('.bash_history', '.zsh_history')
MAX_BYTES = 64 * 1024
MAX_COMMANDS = 200
MAX_LINE = 500
ZSH_EXTENDED = re.compile(r'^: \d+:\d+;(.*)$')
BASH_TIMESTAMP = re.compile(r'^#\d+$')
# Lines that may carry a credential are never shown. Kept in sync with
# SECRET_PATTERN in frontend/src/composables/commandHistory.js.
LOOKS_SECRET = re.compile(
    r'passw|passphrase|secret|token|api[_-]?key|authorization|bearer|credential|private[_-]?key|cookie|sshpass'
    r'|\b\w*(?:pass|pwd)\w*='                       # DB_PASS=…, MYSQL_PWD=…
    r'|://[^\s/:@]+:[^\s/@]+@'                       # https://user:pw@host
    r'|\s-u\s*\S+:\S'                               # curl -u user:pw
    r'|\bsudo\s+(?:-\w+\s+)*-S\b'                   # echo pw | sudo -S
    r'|\b(?:mysql|mariadb|mysqldump|mysqladmin|mongo\w*)\b.*\s-p\S'  # mysql -pSECRET
    r'|\bdocker\s+login\b.*\s(?:-p|--password)\b'
    r'|\bredis-cli\b.*\s-a\s'
    r'|\bsmbclient\b.*%'
    r'|\b(?:gh[pousr]_|github_pat_|glpat-|xox[abprs]-|sk-)[A-Za-z0-9_-]{8,}'
    r'|\bAKIA[0-9A-Z]{16}\b',
    re.IGNORECASE)


def _read_tail(sftp, path):
    size = sftp.lstat(path).st_size or 0
    with sftp.open(path, 'rb') as handle:
        if size > MAX_BYTES:
            handle.seek(size - MAX_BYTES)
            data = handle.read(MAX_BYTES)
            # The first line is probably cut in the middle.
            return data.split(b'\n', 1)[1] if b'\n' in data else b'', True
        return handle.read(MAX_BYTES), False


def _usable(line):
    if not line.strip() or len(line) > MAX_LINE:
        return False
    if any(ord(c) < 32 and c != '\t' or ord(c) == 127 for c in line):
        return False
    return not LOOKS_SECRET.search(line)


def _unmetafy(data):
    """zsh stores bytes 0x83-0x9f as 0x83 followed by (byte ^ 0x20)."""
    if b'\x83' not in data:
        return data
    out, i = bytearray(), 0
    while i < len(data):
        if data[i] == 0x83 and i + 1 < len(data):
            out.append(data[i + 1] ^ 0x20)
            i += 2
        else:
            out.append(data[i])
            i += 1
    return bytes(out)


def _commands(data):
    out = []
    for raw in data.decode('utf-8', errors='replace').split('\n'):
        line = raw.rstrip('\r')
        if BASH_TIMESTAMP.match(line):
            continue
        match = ZSH_EXTENDED.match(line)
        if match:
            line = match.group(1)
        line = line.strip()
        if _usable(line):
            out.append(line)
    return out


def read_shell_history(sftp):
    """Return {'commands': newest first, deduped; 'sources': files read; 'truncated': bool}."""
    home = safe_path(sftp)
    sources, ordered, truncated = [], [], False
    for name in HISTORY_FILES:
        try:
            path = safe_path(sftp, posixpath.join(home, name), kind='file')
            data, cut = _read_tail(sftp, path)
        except (HTTPException, OSError):
            continue  # missing, a symlink, or unreadable: skip that file
        sources.append(name)
        truncated = truncated or cut
        ordered.extend(_commands(_unmetafy(data) if name == '.zsh_history' else data))
    commands, seen = [], set()
    for line in reversed(ordered):
        if line in seen:
            continue
        seen.add(line)
        commands.append(line)
    if len(commands) > MAX_COMMANDS:
        commands, truncated = commands[:MAX_COMMANDS], True
    return {'commands': commands, 'sources': sources, 'truncated': truncated}
