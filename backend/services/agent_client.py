"""Client for the server Agent: one JSON line to `panellab-agentctl` over pinned SSH.

Secrets pass through untouched: they are never logged, cached or put in error text,
and raw stdout/stderr from the server never reaches an error message.
"""
import hashlib
import io
import json
import re
import shlex
import tarfile
import time
from pathlib import Path

import paramiko

from services.remote_connection import CONNECT_TIMEOUT, fail, open_remote, remote_deadline

PROTOCOL_VERSION = 1
CTL_PATH = '/usr/local/bin/panellab-agentctl'
CALL_TIMEOUT = 30
LONG_TIMEOUT = {'alerts.test_email': 130}  # the agent's SMTP timeout plus margin
STDOUT_LIMIT = 2 * 1024 * 1024
STDERR_LIMIT = 64 * 1024
BUNDLE_LIMIT = 2 * 1024 * 1024
AGENT_DIR = Path(__file__).resolve().parents[2] / 'agent'
BUNDLE_ROOTS = ('install.sh', 'systemd', 'panellab_agent')
VERSION_RE = re.compile(r'^__version__\s*=\s*["\']([0-9A-Za-z.+_-]{1,64})["\']\s*$', re.M)
UNRECOGNIZED = '服务器 Agent 返回了无法识别的响应'
PERMISSION_HINT = '请重新登录 SSH 或确认已加入 panellab 组'
UPGRADE_HINT = '请升级服务器上的 Agent'
KNOWN_AGENT_ERRORS = {'bad_request', 'protocol_mismatch', 'not_found', 'conflict', 'invalid', 'draining', 'internal'}


def _agent_error_status(code):
    if code == 'conflict':
        return 409
    if code in ('invalid', 'bad_request'):
        return 422
    if code == 'not_found':
        return 404
    if code == 'internal':
        return 502
    return 503  # draining, protocol_mismatch: 409 is reserved for revision conflicts


def _stderr_code(stderr):
    try:
        value = json.loads(stderr.decode('utf-8'))
        code = value.get('code') if isinstance(value, dict) else None
        return code if isinstance(code, str) else None
    except (ValueError, UnicodeDecodeError):
        return None


def _run(client, command, payload, timeout, *, what='agent'):
    """Run one fixed command, optionally feeding payload on stdin. Returns (status, stdout, stderr)."""
    channel = client.get_transport().open_session(timeout=CONNECT_TIMEOUT)
    deadline = time.monotonic() + timeout
    stdout = bytearray()
    stderr = bytearray()
    try:
        channel.settimeout(CONNECT_TIMEOUT)
        channel.exec_command(command)
        if payload is not None:
            channel.sendall(payload)
        channel.shutdown_write()
        while True:
            if time.monotonic() >= deadline:
                fail('agent_timeout', '服务器 Agent 响应超时', 503)
            if channel.recv_ready():
                stdout.extend(channel.recv(65536))
                if len(stdout) > STDOUT_LIMIT:
                    fail('agent_output_limit', '服务器 Agent 的输出超出上限', 502)
            if channel.recv_stderr_ready():
                data = channel.recv_stderr(65536)
                room = STDERR_LIMIT - len(stderr)
                if room > 0:
                    stderr.extend(data[:room])
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                break
            time.sleep(0.005)
        return channel.recv_exit_status(), bytes(stdout), bytes(stderr)
    finally:
        channel.close()


def _map_exit(status, stderr):
    code = _stderr_code(stderr)
    text = stderr.decode('utf-8', 'replace').lower()
    if status == 127 or (status not in (0, 2, 3, 4) and 'not found' in text):
        fail('agent_not_installed', '服务器上尚未安装 PanelLab Agent', 404)
    if status == 3 or code == 'agent_unreachable':
        fail('agent_unreachable', '无法连接服务器上的 PanelLab Agent，请确认服务正在运行', 503)
    if status == 4 or code == 'agent_permission':
        fail('agent_permission', PERMISSION_HINT, 403)
    if status != 0:
        fail('agent_protocol', UNRECOGNIZED, 502)


def _parse(stdout):
    try:
        body = json.loads(stdout.decode('utf-8'))
    except (ValueError, UnicodeDecodeError, RecursionError):
        fail('agent_protocol', UNRECOGNIZED, 502)
    if not isinstance(body, dict) or not isinstance(body.get('ok'), bool):
        fail('agent_protocol', UNRECOGNIZED, 502)
    if body['ok']:
        if 'result' not in body or body['result'] is None:
            fail('agent_protocol', UNRECOGNIZED, 502)
        return body['result']
    error = body.get('error')
    # An unknown code means the server speaks something we do not understand (a newer or
    # broken Agent), so it is a bad gateway response, not a client error.
    if not isinstance(error, dict) or error.get('code') not in KNOWN_AGENT_ERRORS or not isinstance(error.get('message'), str):
        fail('agent_protocol', UNRECOGNIZED, 502)
    code = error['code']
    message = UPGRADE_HINT if code == 'protocol_mismatch' else error['message'][:500]
    if code == 'protocol_mismatch' and error['message']:
        message = UPGRADE_HINT + '：' + error['message'][:300]
    fail(code, message, _agent_error_status(code))


def call(db, user, server_id, op, args=None):
    request = json.dumps({'protocol_version': PROTOCOL_VERSION, 'op': op, 'args': args or {}},
                         ensure_ascii=False, separators=(',', ':')).encode('utf-8') + b'\n'
    timeout = LONG_TIMEOUT.get(op, CALL_TIMEOUT)
    with open_remote(db, user, server_id) as client:
        try:
            with remote_deadline(client, timeout):
                status, stdout, stderr = _run(client, CTL_PATH, request, timeout)
        except (paramiko.SSHException, OSError, EOFError):
            fail('agent_unreachable', '与服务器的 SSH 连接中断', 503)
    _map_exit(status, stderr)
    return _parse(stdout)


# ------------------------------------------------------------------ install bundle

def agent_version():
    match = VERSION_RE.search((AGENT_DIR / 'panellab_agent' / '__init__.py').read_text(encoding='utf-8'))
    if not match:
        fail('agent_bundle_missing', '找不到 Agent 版本', 500)
    return match.group(1)


STRAY_DIR_RE = re.compile(r'^tmp[a-z0-9_]{6,}$')


def _excluded(parts):
    # Only top-level stray test directories are dropped; a module such as tmp_utils.py stays.
    if STRAY_DIR_RE.fullmatch(parts[0]) and (AGENT_DIR / parts[0]).is_dir():
        return True
    return any(p in ('__pycache__', 'tests') or p.endswith('.pyc') for p in parts)


def _bundle_files():
    found = []
    for root in BUNDLE_ROOTS:
        base = AGENT_DIR / root
        if base.is_file():
            found.append(Path(root))
        elif base.is_dir():
            for path in base.rglob('*'):
                rel = path.relative_to(AGENT_DIR)
                if path.is_file() and not path.is_symlink() and not _excluded(rel.parts):
                    found.append(rel)
    return sorted(found, key=lambda p: p.as_posix())


def build_bundle():
    """Deterministic in-memory tar of the agent: fixed order, root owner, normalized modes."""
    files = _bundle_files()
    if not any(p.as_posix() == 'install.sh' for p in files):
        fail('agent_bundle_missing', '仓库中缺少 Agent 安装包', 500)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w', format=tarfile.USTAR_FORMAT) as tar:
        dirs = sorted({d.as_posix() for p in files for d in list(p.parents)[:-1]})
        entries = [(d, None) for d in dirs] + [(p.as_posix(), p) for p in files]
        for name, source in sorted(entries):
            info = tarfile.TarInfo(name)
            info.mtime, info.uid, info.gid, info.uname, info.gname = 0, 0, 0, 'root', 'root'
            if source is None:
                info.type, info.mode = tarfile.DIRTYPE, 0o755
                tar.addfile(info)
            else:
                data = (AGENT_DIR / source).read_bytes()
                info.size = len(data)
                info.mode = 0o755 if name == 'install.sh' else 0o644
                tar.addfile(info, io.BytesIO(data))
    blob = buffer.getvalue()
    if len(blob) >= BUNDLE_LIMIT:
        fail('agent_bundle_too_large', 'Agent 安装包过大', 500)
    return blob


def upload_bundle(db, user, server_id):
    version = agent_version()
    blob = build_bundle()
    digest = hashlib.sha256(blob).hexdigest()[:12]
    with open_remote(db, user, server_id) as client:
        try:
            with remote_deadline(client, CALL_TIMEOUT):
                with client.open_sftp() as sftp:
                    sftp.get_channel().settimeout(CONNECT_TIMEOUT)
                    home = sftp.normalize('.')
                    if not isinstance(home, str) or not home.startswith('/'):
                        fail('agent_install_failed', '无法确定服务器上的主目录', 502)
                    # Content-addressed: different bundle contents never share a directory.
                    name = 'panellab-agent-%s-%s' % (version, digest)
                    target = '%s/%s' % (home.rstrip('/'), name)
                    tar_path = target + '.tar'
                    sftp.putfo(io.BytesIO(blob), tar_path)
                q = shlex.quote
                command = 'rm -rf %s && mkdir -p %s && tar -xf %s -C %s && rm -f %s' % (
                    q(target), q(target), q(tar_path), q(target), q(tar_path))
                status, _, _ = _run(client, command, None, CALL_TIMEOUT)
        except (paramiko.SSHException, OSError, EOFError):
            fail('agent_install_failed', '上传 Agent 安装包失败', 502)
    if status != 0:
        fail('agent_install_failed', '在服务器上解包 Agent 安装包失败', 502)
    return {'path': target, 'command': 'sudo bash ' + shlex.quote(target + '/install.sh'), 'version': version}
