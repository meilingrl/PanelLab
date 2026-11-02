"""Owner-scoped workload metadata and bounded remote observations."""
import posixpath
import stat
from contextlib import contextmanager
from datetime import datetime, timezone
from fastapi import HTTPException
from models.workload import Workload
from services import remote_connection as connection, remote_files, remote_probe
from services.remote_connection import fail

TEXT_LIMIT = 64 * 1024
DOWNLOAD_LIMIT = 16 * 1024 * 1024
BINDING_FIELDS = {'server_id', 'root_path', 'log_path', 'pid', 'process_identity'}


def owned(db, user, workload_id):
    row = db.query(Workload).filter(Workload.id == workload_id, Workload.user_id == user.id).first()
    if row is None: fail('workload_not_found', 'Workload not found', 404)
    connection.owned_server(db, user, row.server_id)
    return row


def contained(path, root):
    if not isinstance(path, str) or not path.startswith('/') or len(path) > 4096 or any(ord(c) < 32 for c in path) or '..' in path.split('/'):
        fail('invalid_path', 'An absolute path without traversal or controls is required', 403)
    path = posixpath.normpath(path)
    if path != root and not path.startswith(root.rstrip('/') + '/'):
        fail('path_outside_root', 'Path must stay within the workload root', 403)
    return path


@contextmanager
def file_errors():
    try: yield
    except HTTPException as exc:
        if exc.status_code == 400: raise HTTPException(403, detail=exc.detail) from None
        raise
    except FileNotFoundError: fail('path_unavailable', 'Remote file does not exist', 404)
    except PermissionError: fail('path_unavailable', 'Remote file is inaccessible', 403)


def validate_binding(db, user, data):
    connection.owned_server(db, user, data.server_id)
    if data.log_path is not None: contained(data.log_path, data.root_path)
    with connection.open_remote(db, user, data.server_id) as client:
        with remote_files.open_sftp(client) as sftp, file_errors():
            remote_files.safe_path(sftp, data.root_path)
            if data.log_path:
                remote_files.safe_path(sftp, data.log_path, root=data.root_path, kind='file')
        if data.pid is not None:
            target = remote_probe.collect_snapshot(client, pid=data.pid).get('target_process')
            if not target or target['status'] == 'unavailable':
                fail('process_unavailable', 'Selected process identity cannot be verified', 503)
            if target['status'] != 'present' or target['process']['identity'] != data.process_identity:
                fail('process_changed', 'Selected process has exited or its identity changed', 409)


def serialize(row):
    result = {}
    for column in row.__table__.columns:
        value = getattr(row, column.name)
        if isinstance(value, datetime): value = value.replace(tzinfo=timezone.utc).isoformat()
        result[column.name] = value
    return result


def observation(db, user, row):
    with connection.open_remote(db, user, row.server_id) as client:
        snapshot = remote_probe.collect_snapshot(client, pid=row.pid)
        warnings = list(snapshot['warnings'])
        process = None
        status = 'unbound' if row.pid is None else 'unknown'
        target = snapshot.get('target_process')
        if row.pid is not None and target:
            if target['status'] == 'missing': status = 'exited_or_missing'
            elif target['status'] == 'present':
                if target['process']['identity'] == row.process_identity:
                    process = target['process']
                    process_state = process.get('state')
                    if process_state in ('Z', 'X', 'x'):
                        status = 'exited_or_missing'
                        warnings.append('Bound process is a zombie or dead process')
                    elif process_state in ('T', 't'):
                        status = 'unknown'
                        warnings.append('Bound process is stopped or being traced; execution is not running')
                    else: status = 'running'
                else:
                    status = 'exited_or_missing'
                    warnings.append('PID identity changed; the original process is no longer present')
            else: warnings.append('Bound process is unavailable; its state is unknown')
        log = {'path':row.log_path, 'available':False, 'modified_at':None, 'size':None}
        if row.log_path:
            try:
                with remote_files.open_sftp(client) as sftp, file_errors():
                    path = remote_files.safe_path(sftp, row.log_path, root=row.root_path, kind='file')
                    info = sftp.lstat(path)
                    log.update(available=True, modified_at=info.st_mtime, size=info.st_size)
            except HTTPException as exc:
                if exc.status_code >= 500: raise
                warnings.append('Log file is unavailable')
    return {'workload_id':row.id,'observed_at':snapshot['observed_at'],'process_status':status,
            'process':process,'system':snapshot['system'],'log':log,'warnings':warnings}


def files(db, user, row, path):
    path = contained(path or row.root_path, row.root_path)
    with connection.open_remote(db, user, row.server_id) as client:
        with remote_files.open_sftp(client) as sftp, file_errors():
            return remote_files.list_directory(sftp, path, root=row.root_path)


def read_file(db, user, row, path, *, download=False):
    path = contained(path, row.root_path)
    limit = DOWNLOAD_LIMIT if download else TEXT_LIMIT
    with connection.open_remote(db, user, row.server_id) as client:
        with remote_files.open_sftp(client) as sftp, file_errors():
            path = remote_files.safe_path(sftp, path, root=row.root_path, kind='file')
            with sftp.open(path, 'rb') as stream:
                before = stream.stat()
                if not stat.S_ISREG(before.st_mode or 0): fail('invalid_file_type', 'Expected a regular file', 403)
                if before.st_size is None or before.st_size < 0: fail('file_size_unavailable', 'File size unavailable', 503)
                if download and before.st_size > limit: fail('file_too_large', 'Download exceeds 16 MiB', 413)
                offset = 0 if download else max(0, before.st_size-limit)
                stream.seek(offset)
                # Read at most one extra byte to detect growth, never stream without a bound.
                data = stream.read(limit+1)
                after = stream.stat()
                if not stat.S_ISREG(after.st_mode or 0): fail('file_changed', 'File changed during read', 409)
                if download and (len(data)>limit or after.st_size>limit): fail('file_too_large', 'Download exceeds 16 MiB', 413)
                changed = (before.st_size, before.st_mtime) != (after.st_size, after.st_mtime) or len(data) != min(limit+1, max(0,before.st_size-offset))
                # Revalidate the named path after reading; SFTP cannot make this atomic.
                remote_files.safe_path(sftp,path,root=row.root_path,kind='file')
                metadata = {'path':path,'truncated':offset>0 or len(data)>limit,'size':after.st_size,
                            'modified_at':after.st_mtime,'changed_during_read':changed}
                return data[:limit], metadata
