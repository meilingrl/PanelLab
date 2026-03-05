"""系统监控：CPU、内存、磁盘、网络快照（本机 + 可选远程 Linux）。"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

try:
    from typing import Annotated
except ImportError:
    from typing_extensions import Annotated

import psutil
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from config import get_settings
from database import get_db
from models.monitor_remote import MonitorRemoteConfig
from models.user import User
from routers.auth import get_current_user
from utils.cipher import decrypt_password, encrypt_password

router = APIRouter(prefix="/api/monitor", tags=["monitor"])


class RemoteConfigBody(BaseModel):
    host: str = Field(..., min_length=1, max_length=255, description="主机 IP 或域名")
    port: int = Field(22, ge=1, le=65535)
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, description="SSH 登录密码")

# 在远程 Linux 上通过 python3 - 从 stdin 执行的采集脚本，需输出与本机同结构的 JSON
REMOTE_SCRIPT = """
import json, psutil
try:
    cpu = psutil.cpu_percent(interval=None)
except Exception:
    cpu = None
try:
    v = psutil.virtual_memory()
    mem = {"total_mb": round(v.total/(1024*1024),1), "available_mb": round(v.available/(1024*1024),1), "used_mb": round(v.used/(1024*1024),1), "percent": round(v.percent,1)}
except Exception:
    mem = None
disk = []
try:
    for p in psutil.disk_partitions(all=False):
        try:
            u = psutil.disk_usage(p.mountpoint)
            disk.append({"mountpoint": p.mountpoint, "total_gb": round(u.total/(1024**3),2), "used_gb": round(u.used/(1024**3),2), "free_gb": round(u.free/(1024**3),2), "percent": round(u.percent,1)})
        except (PermissionError, OSError):
            pass
    if not disk:
        u = psutil.disk_usage("/")
        disk = [{"mountpoint": "/", "total_gb": round(u.total/(1024**3),2), "used_gb": round(u.used/(1024**3),2), "free_gb": round(u.free/(1024**3),2), "percent": round(u.percent,1)}]
except Exception:
    pass
try:
    n = psutil.net_io_counters()
    net = {"bytes_sent": n.bytes_sent, "bytes_recv": n.bytes_recv} if n else None
except Exception:
    net = None
print(json.dumps({"cpu_percent": cpu, "memory": mem, "disk": disk, "network": net}))
"""


def _get_cpu() -> Optional[float]:
    try:
        return psutil.cpu_percent(interval=None)
    except Exception:
        return None


def _get_memory() -> Optional[Dict[str, Any]]:
    try:
        v = psutil.virtual_memory()
        return {
            "total_mb": round(v.total / (1024 * 1024), 1),
            "available_mb": round(v.available / (1024 * 1024), 1),
            "used_mb": round(v.used / (1024 * 1024), 1),
            "percent": round(v.percent, 1),
        }
    except Exception:
        return None


def _get_disk() -> List[Dict[str, Any]]:
    result = []
    try:
        for part in psutil.disk_partitions(all=False):
            try:
                u = psutil.disk_usage(part.mountpoint)
                result.append({
                    "mountpoint": part.mountpoint,
                    "total_gb": round(u.total / (1024 ** 3), 2),
                    "used_gb": round(u.used / (1024 ** 3), 2),
                    "free_gb": round(u.free / (1024 ** 3), 2),
                    "percent": round(u.percent, 1),
                })
            except (PermissionError, OSError):
                continue
        if not result:
            # 至少尝试根分区
            root = "C:\\" if os.name == "nt" else "/"
            u = psutil.disk_usage(root)
            result.append({
                "mountpoint": root,
                "total_gb": round(u.total / (1024 ** 3), 2),
                "used_gb": round(u.used / (1024 ** 3), 2),
                "free_gb": round(u.free / (1024 ** 3), 2),
                "percent": round(u.percent, 1),
            })
    except Exception:
        pass
    return result


def _get_network() -> Optional[Dict[str, Any]]:
    try:
        n = psutil.net_io_counters()
        if n is None:
            return None
        return {
            "bytes_sent": n.bytes_sent,
            "bytes_recv": n.bytes_recv,
        }
    except Exception:
        return None


def _get_stats_local() -> Dict[str, Any]:
    return {
        "cpu_percent": _get_cpu(),
        "memory": _get_memory(),
        "disk": _get_disk(),
        "network": _get_network(),
    }


def _ssh_run_and_get_stats(host: str, port: int, username: str, password: str) -> Optional[Dict[str, Any]]:
    """通过 SSH 在指定主机上执行采集脚本，返回同结构的 JSON。"""
    try:
        import paramiko
    except ImportError:
        return None
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=host,
            port=port,
            username=username,
            password=password,
            timeout=15,
            allow_agent=False,
            look_for_keys=False,
        )
        chan = client.get_transport().open_session()
        chan.setblocking(True)
        chan.exec_command("python3 -")
        chan.sendall(REMOTE_SCRIPT.encode("utf-8"))
        chan.shutdown_write()
        stdout = b""
        while True:
            buf = chan.recv(4096)
            if not buf:
                break
            stdout += buf
        chan.close()
        client.close()
        out = stdout.decode("utf-8", errors="replace").strip()
        if not out:
            return None
        return json.loads(out)
    except Exception:
        return None


@router.get("/remote-config")
def get_remote_config(
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """返回当前保存的远程连接配置（不含密码）。"""
    row = db.query(MonitorRemoteConfig).first()
    if not row:
        return {"configured": False}
    return {
        "configured": True,
        "host": row.host,
        "port": row.port,
        "username": row.username,
    }


@router.put("/remote-config")
def put_remote_config(
    body: RemoteConfigBody,
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """保存远程连接配置（IP、端口、用户名、密码），密码加密存储。"""
    settings = get_settings()
    encrypted = encrypt_password(settings.secret_key, body.password)
    row = db.query(MonitorRemoteConfig).first()
    if row:
        row.host = body.host.strip()
        row.port = body.port
        row.username = body.username.strip()
        row.password_encrypted = encrypted
    else:
        row = MonitorRemoteConfig(
            host=body.host.strip(),
            port=body.port,
            username=body.username.strip(),
            password_encrypted=encrypted,
        )
        db.add(row)
    db.commit()
    return {"message": "已保存，可在上方选择「远程服务器」查看监控。"}


@router.get("/hosts")
def get_hosts(_user: Annotated[User, Depends(get_current_user)]):
    """返回可选的监控目标列表（本机 + 远程）。下拉框始终显示两项，未配置时选远程会提示在页面下方配置。"""
    return {
        "hosts": [
            {"id": "local", "label": "本机"},
            {"id": "remote", "label": "远程服务器"},
        ]
    }


@router.get("/stats")
def get_stats(
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    target: str = Query("local", description="local | remote"),
):
    """返回指定目标（本机或远程）的 CPU、内存、磁盘、网络快照，需登录。"""
    if target == "remote":
        row = db.query(MonitorRemoteConfig).first()
        if not row:
            return JSONResponse(
                status_code=503,
                content={"detail": "请先在下方配置远程服务器连接。"},
            )
        settings = get_settings()
        password = decrypt_password(settings.secret_key, row.password_encrypted)
        if not password:
            return JSONResponse(
                status_code=503,
                content={"detail": "远程配置已损坏，请重新保存连接信息。"},
            )
        data = _ssh_run_and_get_stats(row.host, row.port, row.username, password)
        if data is None:
            return JSONResponse(
                status_code=503,
                content={"detail": "远程服务器暂不可用，请检查 IP、端口、用户名与密码及网络。"},
            )
        return data
    return _get_stats_local()
