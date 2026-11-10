"""系统监控：本机快照，以及经固定主机指纹连接的共享服务器快照。"""
from __future__ import annotations

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
from sqlalchemy.orm import Session

from database import get_db
from models.monitor_remote import MonitorRemoteConfig
from models.server import Server
from models.user import User
from routers.auth import get_current_user
from services.remote_connection import connection_summary, fail

router = APIRouter(prefix="/api/monitor", tags=["monitor"])

# 本机网络速率与历史（内存，进程重启清空）
_net_last_ts: Optional[float] = None
_net_last_sent: Optional[int] = None
_net_last_recv: Optional[int] = None
_network_history: deque = deque(maxlen=60)


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


@router.get("/remote-config")
def get_legacy_remote_config(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Report a pre-workspace remote config so the user can delete it. It is never used to connect."""
    row = db.query(MonitorRemoteConfig).filter(MonitorRemoteConfig.user_id == current_user.id).first()
    if not row:
        return {"legacy_configured": False}
    return {"legacy_configured": True, "host": row.host, "port": row.port, "username": row.username}


@router.delete("/remote-config")
def delete_legacy_remote_config(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Remove the legacy record, including its stored encrypted password."""
    db.query(MonitorRemoteConfig).filter(MonitorRemoteConfig.user_id == current_user.id).delete()
    db.commit()
    return {"legacy_configured": False}


@router.get("/hosts")
def get_hosts(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """本机 + 当前用户在任务工作区添加的服务器；只有已确认指纹的服务器可以采集。"""
    hosts = [{"id": "local", "label": "本机", "pinned": True}]
    rows = db.query(Server).filter(Server.user_id == current_user.id).order_by(Server.name).all()
    for row in rows:
        pinned = connection_summary(db, current_user, row.id)["pinned"]
        hosts.append({"id": f"server:{row.id}", "label": f"{row.name} · {row.host}", "pinned": pinned})
    return {"hosts": hosts}


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
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    target: str = Query("local", max_length=32, description="local | server:<id>"),
):
    """本机或已确认指纹的共享服务器的 CPU、内存、磁盘、网络快照。"""
    if target == "local":
        return _get_stats_local()
    if target == "remote":
        fail("legacy_remote_removed", "旧版远程监控连接已停用，请在任务工作区添加服务器并确认主机指纹", 410)
    prefix, _, value = target.partition(":")
    if prefix != "server" or not value.isdigit() or not 0 < int(value) <= 2**62:
        fail("invalid_target", "Unknown monitor target", 422)
    from services import remote_monitor
    return remote_monitor.stats(db, current_user, int(value))
