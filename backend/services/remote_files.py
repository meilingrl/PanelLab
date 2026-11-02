"""Bounded SFTP reads, with lexical/root and every-component lstat validation.

SFTP has no portable openat/O_NOFOLLOW: a hostile concurrent remote rename can
race these checks. The service never claims atomic remote filesystem snapshots.
"""
import posixpath
import stat
import time
from contextlib import contextmanager
from services.remote_connection import CONNECT_TIMEOUT, fail


@contextmanager
def open_sftp(client):
    with client.open_sftp() as sftp:
        sftp.get_channel().settimeout(CONNECT_TIMEOUT)
        yield sftp


def safe_path(sftp, path=None, *, root=None, kind='directory'):
    if path is None:
        path = sftp.normalize('.')
    if not isinstance(path, str) or len(path) > 4096 or not path.startswith('/') or any(c in path for c in ('\0', '\n', '\r')):
        fail('invalid_path', 'An absolute path without control characters is required', 400)
    parts = path.split('/')
    if '..' in parts:
        fail('invalid_path', 'Parent traversal is not allowed', 400)
    path = posixpath.normpath(path)
    if root is not None:
        root = safe_path(sftp, root, kind='directory')
        if path != root and not path.startswith(root.rstrip('/') + '/'):
            fail('path_outside_root', 'Path must stay within the workload root', 400)
    current = '/'
    components = [p for p in path.split('/') if p]
    try:
        for index, part in enumerate(components):
            current = posixpath.join(current, part)
            info = sftp.lstat(current)
            if stat.S_ISLNK(info.st_mode):
                fail('symlink_not_allowed', 'Symbolic links cannot be followed', 400)
            if index < len(components)-1 and not stat.S_ISDIR(info.st_mode):
                fail('invalid_path', 'Path parent is not a directory', 400)
        info = sftp.lstat(path)
        valid = stat.S_ISDIR(info.st_mode) if kind == 'directory' else stat.S_ISREG(info.st_mode)
        if not valid:
            fail('invalid_file_type', 'Expected a directory or regular file', 400)
        normalized = sftp.normalize(path)
        if normalized != path:
            fail('path_changed', 'Remote path changed during validation', 409)
        return normalized
    except OSError:
        fail('path_unavailable', 'Remote path does not exist or is inaccessible', 404)


def list_directory(sftp, path=None, *, root=None):
    cwd = safe_path(sftp, path, root=root)
    entries = []
    deadline = time.monotonic() + CONNECT_TIMEOUT
    truncated = False
    try:
        for item in sftp.listdir_iter(cwd, read_aheads=1):
            if time.monotonic() > deadline:
                fail('file_timeout', 'Remote directory read timed out', 503)
            if len(entries) >= 500:
                truncated = True
                break
            name = item.filename
            if not name or name in ('.', '..') or '/' in name or '\0' in name:
                continue
            mode = item.st_mode or 0
            kind = 'symlink' if stat.S_ISLNK(mode) else 'directory' if stat.S_ISDIR(mode) else 'file' if stat.S_ISREG(mode) else 'special'
            entries.append({'name': name, 'path': posixpath.join(cwd, name), 'type': kind,
                            'size': item.st_size, 'mtime': item.st_mtime,
                            'disabled': kind in ('symlink', 'special') or any(c in name for c in ('\n', '\r'))})
    except OSError:
        fail('path_unavailable', 'Cannot read remote directory', 503)
    parent = posixpath.dirname(cwd) if cwd != '/' and cwd != root else None
    return {'cwd': cwd, 'parent': parent, 'entries': entries, 'truncated': truncated}
