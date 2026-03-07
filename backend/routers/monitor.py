"""系统监控：CPU、内存、磁盘、网络快照（本机 + 可选远程 Linux）。"""
from __future__ import annotations

import json
import os
import time
from collections import deque
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

# 本机网络速率与历史（内存，进程重启清空）
_net_last_ts: Optional[float] = None
_net_last_sent: Optional[int] = None
_net_last_recv: Optional[int] = None
_network_history: deque = deque(maxlen=60)


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
    global _net_last_ts, _net_last_sent, _net_last_recv
    net = _get_network()
    if net:
        now = time.time()
        if _net_last_ts is not None and _net_last_sent is not None and _net_last_recv is not None:
            interval = now - _net_last_ts
            if interval > 0:
                rate_sent_kbps = (net["bytes_sent"] - _net_last_sent) / 1024.0 / interval
                rate_recv_kbps = (net["bytes_recv"] - _net_last_recv) / 1024.0 / interval
                net["rate_sent_kbps"] = round(rate_sent_kbps, 2)
                net["rate_recv_kbps"] = round(rate_recv_kbps, 2)
                _network_history.append({
                    "ts": int(now),
                    "rate_sent_kbps": round(rate_sent_kbps, 2),
                    "rate_recv_kbps": round(rate_recv_kbps, 2),
                })
        net["network_history"] = list(_network_history)
        _net_last_ts, _net_last_sent, _net_last_recv = now, net["bytes_sent"], net["bytes_recv"]
    return {
        "cpu_percent": _get_cpu(),
        "memory": _get_memory(),
        "disk": _get_disk(),
        "network": net,
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


def _ssh_install_psutil(host: str, port: int, username: str, password: str) -> Dict[str, Any]:
    """通过 SSH 在目标机安装 psutil：优先 apt（Debian/Ubuntu），失败再试 pip。返回 { "ok": bool, "message"?: str, "detail"?: str }。"""
    try:
        import paramiko
    except ImportError:
        return {"ok": False, "detail": "后端未安装 paramiko，无法执行远程命令"}
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

        def run_cmd(command: str, timeout: int = 90) -> tuple[int, str, str]:
            stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
            out = (stdout.read() or b"").decode("utf-8", errors="replace").strip()
            err = (stderr.read() or b"").decode("utf-8", errors="replace").strip()
            code = stdout.channel.recv_exit_status()
            return code, out, err

        # 1. 优先用 apt（Debian/Ubuntu），不依赖 pip，且不受 PEP 668 限制
        apt_cmd = "DEBIAN_FRONTEND=noninteractive apt-get update -qq && apt-get install -y python3-psutil 2>&1"
        code, out, err = run_cmd(apt_cmd)
        combined_apt = (out + "\n" + err).lower()
        if code == 0:
            client.close()
            return {"ok": True, "message": "已通过 apt 在远程主机安装或确认 python3-psutil"}
        if "is already the newest" in combined_apt or "already installed" in combined_apt or "0 upgraded" in combined_apt:
            client.close()
            return {"ok": True, "message": "远程主机已安装 python3-psutil，无需重复安装"}

        # 2. apt 不可用（非 Debian/无权限/无包）时再试 pip
        pip_cmd = "python3 -m pip install --user psutil 2>&1 || pip3 install --user psutil 2>&1"
        code, out, err = run_cmd(pip_cmd, timeout=60)
        client.close()
        combined = (out + "\n" + err).lower()
        if code == 0 or "Successfully installed" in out or "already satisfied" in combined:
            return {"ok": True, "message": "已在远程主机成功安装或已存在 psutil"}
        if "externally-managed-environment" in combined or "externally managed" in combined:
            return {
                "ok": True,
                "message": "远程为受管 Python 环境，已收到反馈。若需使用监控，请在该主机执行: apt install python3-psutil",
            }
        return {"ok": False, "detail": (out or err) or f"退出码 {code}"}
    except Exception as e:
        return {"ok": False, "detail": str(e)}


@router.post("/remote-install-psutil")
def post_remote_install_psutil(
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """在已配置的远程主机上执行安装 psutil；未配置返回 503。"""
    row = db.query(MonitorRemoteConfig).first()
    if not row:
        return JSONResponse(
            status_code=503,
            content={"ok": False, "detail": "请先保存远程服务器连接配置。"},
        )
    settings = get_settings()
    password = decrypt_password(settings.secret_key, row.password_encrypted)
    if not password:
        return JSONResponse(
            status_code=503,
            content={"ok": False, "detail": "远程配置已损坏，请重新保存连接信息。"},
        )
    result = _ssh_install_psutil(row.host, row.port, row.username, password)
    if result["ok"]:
        return result
    return JSONResponse(status_code=502, content=result)


@router.get("/hosts")
def get_hosts(_user: Annotated[User, Depends(get_current_user)]):
    """返回可选的监控目标列表（本机 + 远程）。下拉框始终显示两项，未配置时选远程会提示在页面下方配置。"""
    return {
        "hosts": [
            {"id": "local", "label": "本机"},
            {"id": "remote", "label": "远程服务器"},
        ]
    }


def _get_processes_list(limit: int = 50, sort_by: str = "cpu_percent", name_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    """本机进程列表，用于进程管理展示。"""
    result: List[Dict[str, Any]] = []
    try:
        procs = []
        for p in psutil.process_iter(["pid", "name", "status", "username", "memory_info", "cpu_percent"]):
            try:
                pinfo = p.info
                if pinfo.get("pid") is None:
                    continue
                name = (pinfo.get("name") or "").strip() or str(pinfo.get("pid"))
                if name_filter and name_filter.strip():
                    if name_filter.strip().lower() not in name.lower():
                        continue
                mem_mb = None
                if pinfo.get("memory_info"):
                    mem_mb = round(pinfo["memory_info"].rss / (1024 * 1024), 2)
                cpu = pinfo.get("cpu_percent")
                if cpu is None:
                    try:
                        cpu = p.cpu_percent(interval=None)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        cpu = 0
                procs.append({
                    "pid": pinfo["pid"],
                    "name": name,
                    "status": (pinfo.get("status") or "unknown"),
                    "username": pinfo.get("username") or "—",
                    "memory_mb": mem_mb,
                    "cpu_percent": round(cpu, 1) if cpu is not None else None,
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError):
                continue
        # 排序：cpu_percent | memory_mb | name，默认 cpu 降序
        key_order = "cpu_percent"
        reverse = True
        if sort_by == "memory_mb":
            key_order = "memory_mb"
            reverse = True
        elif sort_by == "name":
            key_order = "name"
            reverse = False
        elif sort_by == "pid":
            key_order = "pid"
            reverse = False
        procs.sort(key=lambda x: (x.get(key_order) is None, x.get(key_order) if key_order != "name" else (x.get(key_order) or "").lower()), reverse=reverse)
        result = procs[: max(1, min(limit, 500))]
    except Exception:
        pass
    return result


@router.get("/processes")
def get_processes(
    _user: Annotated[User, Depends(get_current_user)],
    limit: int = Query(50, ge=1, le=500, description="返回条数"),
    sort_by: str = Query("cpu_percent", description="排序: cpu_percent | memory_mb | name | pid"),
    name_filter: Optional[str] = Query(None, description="按进程名模糊过滤"),
):
    """返回本机进程列表（只读），需登录。仅支持本机，远程暂不提供。"""
    return {"processes": _get_processes_list(limit=limit, sort_by=sort_by, name_filter=name_filter)}


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
