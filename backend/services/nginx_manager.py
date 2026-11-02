"""Bounded Nginx file transactions. Callers hold change_lock through DB commit."""
from __future__ import annotations
import hashlib
import os
import re
import shlex
import stat
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from config import get_settings
from services.site_validation import validate_config

MAX_CONFIG_BYTES = 1024 * 1024
MAX_DIRECTORY_BYTES = 8 * 1024 * 1024
MAX_CONFIG_FILES = 512
_PROCESS_LOCK = threading.RLock()

class NginxManagerError(Exception):
    def __init__(self, detail):
        super().__init__(detail)
        self.detail = detail

@dataclass
class SiteRuntimeConfig:
    id: int
    name: str
    domain: str
    site_type: str
    root_path: Optional[str]
    proxy_target: Optional[str]
    listen_port: int
    enabled: bool
    config_filename: Optional[str]

@dataclass
class ChangeResult:
    status: str
    steps: list
    message: str

class NginxManager:
    def __init__(self):
        self.settings = get_settings()

    def config_dir(self):
        path = Path(os.path.abspath(self.settings.nginx_conf_dir))
        # Do not silently canonicalize a symlink into an unrelated directory.
        if any(p.is_symlink() for p in [path, *path.parents]):
            raise NginxManagerError('配置目录不能包含符号链接')
        if path.exists() and not path.is_dir():
            raise NginxManagerError('配置目录不可用')
        return path

    def path(self, filename):
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,200}\.conf', filename or ''):
            raise NginxManagerError('配置文件名必须是受管 .conf basename')
        path = self.config_dir() / filename
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise NginxManagerError('配置文件不能是符号链接或特殊文件')
        return path

    def get_config_filename(self, site):
        filename = site.config_filename or f'site-{site.id}.conf'
        self.path(filename)
        return filename

    def _read(self, path):
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_CONFIG_BYTES:
                    raise NginxManagerError('配置文件类型或大小超出安全限制')
                content = stream.read(MAX_CONFIG_BYTES + 1)
                if len(content) > MAX_CONFIG_BYTES:
                    raise NginxManagerError('配置文件超出大小限制')
                return content
        except FileNotFoundError:
            return None
        except OSError:
            raise NginxManagerError('无法安全读取配置文件') from None

    def read_config(self, filename):
        content = self._read(self.path(filename))
        try:
            return content.decode('utf-8') if content is not None else None
        except UnicodeDecodeError:
            raise NginxManagerError('配置文件不是 UTF-8 文本') from None

    def fingerprint(self):
        directory = self.config_dir()
        digest = hashlib.sha256(b'exists' if directory.exists() else b'missing')
        try:
            names = sorted(p.name for p in directory.glob('*.conf'))
            if len(names) > MAX_CONFIG_FILES:
                raise NginxManagerError('配置目录文件数超出安全限制')
            size = 0
            for name in names:
                content = self._read(self.path(name))
                if content is None:
                    raise NginxManagerError('配置目录正在变化，请重试预览')
                size += len(content)
                if size > MAX_DIRECTORY_BYTES:
                    raise NginxManagerError('配置目录超出大小限制')
                digest.update(name.encode() + b'\0' + hashlib.sha256(content).digest())
            return digest.hexdigest()
        except OSError:
            raise NginxManagerError('无法读取配置目录') from None

    @contextmanager
    def change_lock(self):
        if not _PROCESS_LOCK.acquire(timeout=5):
            raise NginxManagerError('另一个配置变更正在执行，请稍后重试')
        fd = None
        try:
            directory = self.config_dir()
            # Same path for all workers sharing this Nginx directory; preview never opens it.
            lock_path = directory.parent / ('.' + directory.name + '.panellab.lock')
            try:
                import fcntl
            except ImportError:
                if not self.settings.nginx_skip_apply:
                    raise NginxManagerError('此平台不支持实际应用所需的文件锁')
                yield
                return
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            deadline = time.monotonic() + 5
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise NginxManagerError('另一个配置变更正在执行，请稍后重试')
                    time.sleep(0.05)
            yield
        except OSError:
            raise NginxManagerError('配置锁或文件操作失败，需要人工核查') from None
        finally:
            if fd is not None: os.close(fd)
            _PROCESS_LOCK.release()

    def _replace(self, path, content):
        # Revalidate immediately before touching the target.
        self.path(path.name)
        if content is None:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
        fd, temporary = tempfile.mkstemp(prefix='.panellab-', suffix='.tmp', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                os.fchmod(stream.fileno(), mode)
                stream.write(content); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)

    def _run_command(self, command_text, prefix):
        try:
            cmd = shlex.split(command_text)
            if not cmd: raise ValueError()
            # Output may contain credentials from existing handwritten config; discard it.
            result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    timeout=self.settings.nginx_cmd_timeout_seconds, check=False)
        except subprocess.TimeoutExpired:
            raise NginxManagerError(prefix + '：命令超时') from None
        except (OSError, ValueError):
            raise NginxManagerError(prefix + '：命令无法执行') from None
        if result.returncode:
            raise NginxManagerError(prefix + '：命令返回失败')

    def apply_change(self, filename, after):
        path = self.path(filename)
        before = self._read(path)
        if self.settings.nginx_skip_apply:
            return ChangeResult('simulated', [], '模拟完成，未写入配置或执行 Nginx')
        steps = []
        reload_attempted = False
        def step(name, operation):
            try:
                operation()
                steps.append(dict(name=name, status='succeeded', message='步骤完成'))
            except (NginxManagerError, OSError):
                steps.append(dict(name=name, status='failed', message='步骤失败或超时；命令输出不予记录'))
                raise
        try:
            step('write', lambda: self._replace(path, after.encode('utf-8') if after is not None else None))
            step('test', lambda: self._run_command(self.settings.nginx_test_cmd, 'Nginx 配置校验失败'))
            reload_attempted = True
            step('reload', lambda: self._run_command(self.settings.nginx_reload_cmd, 'Nginx 重载失败'))
            return ChangeResult('applied', steps, '配置校验通过，reload 命令成功；未验证 HTTP 可用性或 worker 替换')
        except (NginxManagerError, OSError):
            try:
                step('restore', lambda: self._replace(path, before))
                if reload_attempted:
                    step('restore_test', lambda: self._run_command(self.settings.nginx_test_cmd, '恢复校验失败'))
                    step('restore_reload', lambda: self._run_command(self.settings.nginx_reload_cmd, '恢复重载失败'))
                return ChangeResult('failed_restored', steps, '变更失败，原配置已恢复；如尝试过 reload，恢复校验和 reload 命令已成功')
            except (NginxManagerError, OSError):
                return ChangeResult('recovery_failed', steps, '变更及恢复失败，需要人工核查配置与 Nginx 运行状态')

    def apply_site(self, site):
        after = self.render_site_config(site) if site.enabled else None
        with self.change_lock(): return self.apply_change(self.get_config_filename(site), after)

    def remove_site(self, site):
        with self.change_lock(): return self.apply_change(self.get_config_filename(site), None)

    def render_site_config(self, site):
        try:
            validate_config(site.domain, site.site_type, site.root_path, site.proxy_target, site.listen_port)
        except ValueError as exc:
            raise NginxManagerError(str(exc)) from None
        head = f'server {{\n    listen {site.listen_port};\n    server_name {site.domain};\n\n'
        if site.site_type == 'static':
            return head + f'    root {site.root_path};\n    index index.html index.htm;\n\n    location / {{\n        try_files $uri $uri/ /index.html;\n    }}\n}}\n'
        return head + f'''    location / {{
        proxy_pass {site.proxy_target};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }}
}}
'''
