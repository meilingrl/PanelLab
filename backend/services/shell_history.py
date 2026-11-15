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
LOOKS_SECRET = re.compile(
    r'passw|secret|token|api[_-]?key|authorization|bearer|sshpass|\s-p\S', re.IGNORECASE)


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
        ordered.extend(_commands(data))
    commands, seen = [], set()
    for line in reversed(ordered):
        if line in seen:
            continue
        seen.add(line)
        commands.append(line)
    if len(commands) > MAX_COMMANDS:
        commands, truncated = commands[:MAX_COMMANDS], True
    return {'commands': commands, 'sources': sources, 'truncated': truncated}
