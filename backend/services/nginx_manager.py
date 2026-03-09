"""Nginx 配置生成、校验与重载。"""
from __future__ import annotations

import logging
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from config import get_settings

logger = logging.getLogger(__name__)


class NginxManagerError(Exception):
    """Nginx 管理相关可预期异常。"""

    def __init__(self, detail: str):
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


def _slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "-", text.strip().lower())
    s = s.strip("-")
    return s or "site"


def _validate_proxy_target(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise NginxManagerError("代理目标地址格式错误，仅支持 http/https URL")
    return value


class NginxManager:
    def __init__(self):
        self.settings = get_settings()

    def get_config_filename(self, site: SiteRuntimeConfig) -> str:
        if site.config_filename and site.config_filename.strip():
            return site.config_filename.strip()
        return f"site-{site.id}-{_slug(site.name)}.conf"

    def apply_site(self, site: SiteRuntimeConfig) -> str:
        if self.settings.nginx_skip_apply:
            logger.info("NGINX_SKIP_APPLY=true，跳过 Nginx 生效（site_id=%s）", site.id)
            return "已跳过 Nginx 生效（NGINX_SKIP_APPLY=true）"

        conf_dir = Path(self.settings.nginx_conf_dir).resolve()
        conf_dir.mkdir(parents=True, exist_ok=True)
        filename = self.get_config_filename(site)
        conf_path = conf_dir / filename

        old_exists = conf_path.exists()
        old_content = conf_path.read_text(encoding="utf-8") if old_exists else ""

        try:
            if site.enabled:
                content = self.render_site_config(site)
                conf_path.write_text(content, encoding="utf-8")
            else:
                if conf_path.exists():
                    conf_path.unlink()

            self._run_command(self.settings.nginx_test_cmd, "Nginx 配置校验失败")
            self._run_command(self.settings.nginx_reload_cmd, "Nginx 重载失败")
            return "Nginx 配置已生效"
        except NginxManagerError:
            self._restore(conf_path, old_exists, old_content)
            raise

    def remove_site(self, site: SiteRuntimeConfig) -> str:
        if self.settings.nginx_skip_apply:
            return "已跳过 Nginx 删除（NGINX_SKIP_APPLY=true）"
        conf_dir = Path(self.settings.nginx_conf_dir).resolve()
        filename = self.get_config_filename(site)
        conf_path = conf_dir / filename
        old_exists = conf_path.exists()
        old_content = conf_path.read_text(encoding="utf-8") if old_exists else ""
        try:
            if conf_path.exists():
                conf_path.unlink()
            self._run_command(self.settings.nginx_test_cmd, "Nginx 配置校验失败")
            self._run_command(self.settings.nginx_reload_cmd, "Nginx 重载失败")
            return "站点配置已删除并生效"
        except NginxManagerError:
            self._restore(conf_path, old_exists, old_content)
            raise

    def render_site_config(self, site: SiteRuntimeConfig) -> str:
        if not site.domain or not site.domain.strip():
            raise NginxManagerError("域名不能为空")
        if site.listen_port < 1 or site.listen_port > 65535:
            raise NginxManagerError("监听端口范围非法")
        server_name = site.domain.strip()

        if site.site_type == "static":
            root = (site.root_path or "").strip()
            if not root:
                raise NginxManagerError("静态站点必须填写根目录")
            return (
                "server {\n"
                f"    listen {site.listen_port};\n"
                f"    server_name {server_name};\n\n"
                f"    root {root};\n"
                "    index index.html index.htm;\n\n"
                "    location / {\n"
                "        try_files $uri $uri/ /index.html;\n"
                "    }\n"
                "}\n"
            )

        if site.site_type == "proxy":
            target = _validate_proxy_target((site.proxy_target or "").strip())
            return (
                "server {\n"
                f"    listen {site.listen_port};\n"
                f"    server_name {server_name};\n\n"
                "    location / {\n"
                f"        proxy_pass {target};\n"
                "        proxy_set_header Host $host;\n"
                "        proxy_set_header X-Real-IP $remote_addr;\n"
                "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
                "        proxy_set_header X-Forwarded-Proto $scheme;\n"
                "    }\n"
                "}\n"
            )
        raise NginxManagerError("site_type 只能是 static 或 proxy")

    def _run_command(self, command_text: str, prefix: str):
        cmd = shlex.split(command_text.strip())
        if not cmd:
            raise NginxManagerError(f"{prefix}：命令为空")
        binary = shutil.which(cmd[0])
        if binary is None:
            msg = f"{prefix}：未找到命令 `{cmd[0]}`，请检查 nginx 是否已安装并加入 PATH，或设置 NGINX_SKIP_APPLY=true 跳过（仅用于开发）"
            logger.error("[NginxManager] %s", msg)
            raise NginxManagerError(msg)
        cmd[0] = binary
        logger.debug("[NginxManager] 执行命令：%s", " ".join(cmd))
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.settings.nginx_cmd_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            msg = f"{prefix}：命令执行超时（>{self.settings.nginx_cmd_timeout_seconds}s）"
            logger.error("[NginxManager] %s", msg)
            raise NginxManagerError(msg)
        except Exception as exc:
            msg = f"{prefix}：{exc}"
            logger.error("[NginxManager] %s", msg)
            raise NginxManagerError(msg)

        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            if not detail:
                detail = f"退出码 {result.returncode}"
            logger.error(
                "[NginxManager] %s（returncode=%s）\nstdout: %s\nstderr: %s",
                prefix,
                result.returncode,
                result.stdout.strip(),
                result.stderr.strip(),
            )
            raise NginxManagerError(f"{prefix}：{detail}")

    @staticmethod
    def _restore(conf_path: Path, old_exists: bool, old_content: str):
        try:
            if old_exists:
                conf_path.write_text(old_content, encoding="utf-8")
            elif conf_path.exists():
                conf_path.unlink()
        except Exception:
            # 回滚失败不覆盖主错误，避免掩盖真实失败原因
            pass
