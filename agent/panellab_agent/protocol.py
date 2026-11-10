"""Control protocol: one JSON request in, one JSON response out.

Request  {"protocol_version": 1, "op": str, "args": {...}}
Response {"ok": true, "result": ...} or
         {"ok": false, "error": {"code": str, "message": str}}

Secrets (SMTP password, secret_env values) are write-only: reads report only
whether they are set.
"""
from __future__ import annotations

import re
import sys
import traceback
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from . import PROTOCOL_VERSION, __version__, cron
from .alerts import MailError
from .store import RUN_STATUSES, StoreError
from .watchers import DEFAULTS as WATCH_DEFAULTS

ERROR_CODES = ("bad_request", "protocol_mismatch", "not_found", "conflict", "invalid",
               "draining", "internal")
NEVER_RUNS = "该计划永远不会运行"
INTERNAL_MESSAGE = "内部错误"
SET_MARK = "已设置"
UNSET_MARK = "未设置"
_SECRET_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

PERCENT_KEYS = ("disk_percent", "disk_recover_percent", "cpu_percent", "mem_percent",
                "recover_below_percent")
MINUTE_KEYS = ("sustain_minutes", "recover_minutes", "ssh_window_minutes")
THRESHOLD_RANGES = dict([(k, (1, 100)) for k in PERCENT_KEYS]
                        + [(k, (1, 1440)) for k in MINUTE_KEYS]
                        + [("ssh_failures", (1, 100000))])
SMTP_KEYS = ("host", "port", "security", "username", "from", "to", "password")


class ProtocolError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _bad(message: str) -> ProtocolError:
    return ProtocolError("bad_request", message)


def _invalid(message: str) -> ProtocolError:
    return ProtocolError("invalid", message)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _arg(args: Dict[str, Any], name: str, kind, required: bool = True, default=None):
    if name not in args or args[name] is None:
        if required:
            raise _bad("缺少参数 %s" % name)
        return default
    value = args[name]
    if kind is int:
        ok = _is_int(value)
    else:
        ok = isinstance(value, kind)
    if not ok:
        raise _bad("参数 %s 类型不正确" % name)
    return value


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


# ------------------------------------------------------------------ views

def job_view(agent, job: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(job)
    out["secret_env"] = [{"name": n, "status": SET_MARK}
                         for n in agent.secrets.job_secret_names(job["id"])]
    return out


def alerts_view(agent) -> Dict[str, Any]:
    cfg = agent.alert_config()
    smtp = cfg.get("smtp")
    return {
        "smtp": dict(smtp) if isinstance(smtp, dict) else None,
        "smtp_password": SET_MARK if agent.secrets.smtp_password() else UNSET_MARK,
        "thresholds": dict(cfg.get("thresholds") or {}),
        "heartbeat_url": cfg.get("heartbeat_url"),
        "ssh_enabled": cfg.get("ssh_enabled", WATCH_DEFAULTS["ssh_enabled"]),
    }


# --------------------------------------------------------------- schedule

def _next_runs(schedule: Dict[str, Any], after: datetime, count: int) -> List[datetime]:
    found = set()
    for expr in schedule["cron"]:
        found.update(cron.next_times(cron.parse(expr), after, schedule["tz"], count))
    return sorted(found)[:count]


def _check_runs_eventually(agent, schedule: Any) -> None:
    """R7: a non-empty cron list that never fires is rejected as invalid."""
    if not isinstance(schedule, dict):
        return  # shape errors are the store's to report
    exprs = schedule.get("cron")
    tz = schedule.get("tz")
    if not isinstance(exprs, list) or not exprs or not isinstance(tz, str):
        return
    try:
        if not _next_runs(schedule, agent.clock.now(), 1):
            raise _invalid(NEVER_RUNS)
    except (cron.CronError, TypeError, AttributeError):
        return


def _secret_changes(value: Any, allow_delete: bool) -> Dict[str, Optional[str]]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise _invalid("secret_env 必须是对象")
    for k, v in value.items():
        if not isinstance(k, str) or not _SECRET_NAME_RE.fullmatch(k):
            raise _invalid("secret_env 名称不合法：%r" % (k,))
        if not (isinstance(v, str) or (allow_delete and v is None)):
            raise _invalid("secret_env 的值必须是字符串")
    return value


# --------------------------------------------------------------- job ops

def _get_job(agent, job_id: str) -> Dict[str, Any]:
    job = agent.store.get_job(job_id)
    if job is None:
        raise ProtocolError("not_found", "作业不存在：%s" % job_id)
    return job


def op_status(agent, args):
    return agent.status()


def op_jobs_list(agent, args):
    return {"jobs": [job_view(agent, j) for j in agent.store.list_jobs()]}


def op_jobs_get(agent, args):
    return job_view(agent, _get_job(agent, _arg(args, "id", str)))


def op_jobs_create(agent, args):
    job = _arg(args, "job", dict)
    secrets = _secret_changes(args.get("secret_env"), allow_delete=False)
    job = dict(job)
    if "schedule" not in job:
        job["schedule"] = {"cron": [], "tz": agent.server_tz}
    elif isinstance(job["schedule"], dict) and "tz" not in job["schedule"]:
        job["schedule"] = dict(job["schedule"], tz=agent.server_tz)
    _check_runs_eventually(agent, job["schedule"])
    created = agent.store.create_job(job)
    if secrets:
        agent.secrets.set_job_secrets(created["id"], secrets)
    return job_view(agent, created)


def op_jobs_update(agent, args):
    job_id = _arg(args, "id", str)
    revision = _arg(args, "revision", int)
    changes = _arg(args, "changes", dict)
    secrets = _secret_changes(args.get("secret_env"), allow_delete=True)
    if "schedule" in changes:
        _check_runs_eventually(agent, changes["schedule"])
    updated = agent.store.update_job(job_id, changes, revision)
    if secrets:
        agent.secrets.set_job_secrets(job_id, secrets)
    if updated["paused"]:
        agent.runner.drop_retries(job_id)
    return job_view(agent, updated)


def op_jobs_delete(agent, args):
    job_id = _arg(args, "id", str)
    agent.store.delete_job(job_id)
    agent.runner.drop_retries(job_id)
    names = agent.secrets.job_secret_names(job_id)
    if names:
        agent.secrets.set_job_secrets(job_id, dict((n, None) for n in names))
    agent.alerts.observe("job:" + job_id, False, "作业已删除：%s" % job_id, "",
                         notify_recovery=False)
    return {"deleted": job_id}


def op_jobs_run_now(agent, args):
    job = _get_job(agent, _arg(args, "id", str))
    return {"run_id": agent.run_now(job)}


def op_jobs_pause(agent, args):
    job_id = _arg(args, "id", str)
    agent.store.set_paused(job_id, True)
    agent.runner.drop_retries(job_id)
    return job_view(agent, _get_job(agent, job_id))


def op_jobs_resume(agent, args):
    job_id = _arg(args, "id", str)
    agent.store.set_paused(job_id, False)
    return job_view(agent, _get_job(agent, job_id))


def op_jobs_preview(agent, args):
    schedule = dict(_arg(args, "schedule", dict))
    schedule.setdefault("tz", agent.server_tz)
    exprs = schedule.get("cron")
    if not isinstance(exprs, list) or not all(isinstance(c, str) for c in exprs) \
            or not isinstance(schedule["tz"], str):
        raise _invalid('schedule 必须是 {"cron": [str, ...], "tz": str}')
    try:
        runs = _next_runs(schedule, agent.clock.now(), 3)
        zone = ZoneInfo(schedule["tz"]) if exprs else None
    except cron.CronError as exc:
        raise _invalid("schedule: %s" % exc)
    if exprs and not runs:
        raise _invalid(NEVER_RUNS)
    return {"tz": schedule["tz"],
            "runs": [{"utc": t.astimezone(timezone.utc).isoformat(),
                      "local": t.astimezone(zone).isoformat()} for t in runs]}


# --------------------------------------------------------------- run ops

def op_runs_list(agent, args):
    job_id = _arg(args, "job_id", str)
    status = _arg(args, "status", str, required=False)
    before = _arg(args, "before", int, required=False)
    limit = _clamp(_arg(args, "limit", int, required=False, default=50), 1, 200)
    if status is not None and status not in RUN_STATUSES:
        raise _invalid("未知的运行状态：%s" % status)
    _get_job(agent, job_id)
    return {"runs": agent.store.list_runs(job_id, status=status, limit=limit, before_id=before)}


def op_runs_output(agent, args):
    run_id = _arg(args, "run_id", int)
    offset = _arg(args, "offset", int, required=False, default=0)
    limit = _clamp(_arg(args, "limit", int, required=False, default=65536), 1, 262144)
    if offset < 0:
        raise _invalid("offset 不能为负数")
    return agent.runner.read_output(run_id, offset, limit)


def op_runs_cancel(agent, args):
    run_id = _arg(args, "run_id", int)
    if agent.runner.cancel(run_id):
        return {"cancelled": True}
    if agent.store.get_run(run_id) is None:
        raise ProtocolError("not_found", "运行不存在：%s" % run_id)
    raise ProtocolError("conflict", "该运行已结束或正在取消")


# ------------------------------------------------------------- alert ops

def op_alerts_get(agent, args):
    return alerts_view(agent)


def _has_crlf(s: str) -> bool:
    return "\r" in s or "\n" in s


class _Absent:
    pass


_ABSENT = _Absent()


def _validate_smtp(smtp: Any):
    """Return (config without password, password change or _ABSENT)."""
    if not isinstance(smtp, dict):
        raise _invalid("smtp 必须是对象")
    extra = set(smtp) - set(SMTP_KEYS)
    if extra:
        raise _invalid("smtp 含未知字段：%s" % ", ".join(sorted(extra)))
    host, port, security = smtp.get("host"), smtp.get("port"), smtp.get("security")
    username, sender, to = smtp.get("username"), smtp.get("from"), smtp.get("to")
    if not isinstance(host, str) or not host.strip() or _has_crlf(host):
        raise _invalid("smtp.host 必须是非空字符串")
    if not _is_int(port) or not 1 <= port <= 65535:
        raise _invalid("smtp.port 必须是 1-65535 的整数")
    if security not in ("starttls", "ssl", "none"):
        raise _invalid("smtp.security 必须是 starttls、ssl 或 none")
    if username is not None and (not isinstance(username, str) or _has_crlf(username)):
        raise _invalid("smtp.username 必须是字符串或 null")
    if not isinstance(sender, str) or _has_crlf(sender):
        raise _invalid("smtp.from 必须是字符串")
    if (not isinstance(to, list) or not 1 <= len(to) <= 10
            or not all(isinstance(r, str) and "@" in r and not _has_crlf(r) for r in to)):
        raise _invalid("smtp.to 必须是 1-10 个邮件地址")
    password = smtp.get("password", _ABSENT)
    if password is not _ABSENT and password is not None and not isinstance(password, str):
        raise _invalid("smtp.password 必须是字符串或 null")
    clean = {"host": host, "port": port, "security": security,
             "username": username or None, "from": sender, "to": list(to)}
    return clean, password


def op_alerts_configure(agent, args):
    allowed = {"smtp", "thresholds", "heartbeat_url", "ssh_enabled"}
    extra = set(args) - allowed
    if extra:
        raise _invalid("未知字段：%s" % ", ".join(sorted(extra)))
    changes: Dict[str, Any] = {}
    password: Any = _ABSENT
    if "smtp" in args:
        if args["smtp"] is None:
            changes["smtp"] = None  # clear the mail settings; password untouched
        else:
            changes["smtp"], password = _validate_smtp(args["smtp"])
    if "thresholds" in args:
        th = args["thresholds"]
        if th is None:
            th = {}
        if not isinstance(th, dict):
            raise _invalid("thresholds 必须是对象")
        for k, v in th.items():
            if k not in THRESHOLD_RANGES:
                raise _invalid("未知阈值：%s" % k)
            lo, hi = THRESHOLD_RANGES[k]
            if not _is_num(v) or not lo <= v <= hi:
                raise _invalid("阈值 %s 必须在 %s-%s 之间" % (k, lo, hi))
        changes["thresholds"] = dict(th)
    if "heartbeat_url" in args:
        url = args["heartbeat_url"]
        if url not in (None, ""):
            if (not isinstance(url, str) or not url.startswith("https://")
                    or len(url) > 2048 or any(c.isspace() for c in url)):
                raise _invalid("heartbeat_url 必须是 https:// 地址（最长 2048 字符）")
        changes["heartbeat_url"] = url or None
    if "ssh_enabled" in args:
        if not isinstance(args["ssh_enabled"], bool):
            raise _invalid("ssh_enabled 必须是布尔值")
        changes["ssh_enabled"] = args["ssh_enabled"]
    agent.update_alert_config(changes)
    if password is not _ABSENT:
        agent.secrets.set_smtp_password(password)
    return alerts_view(agent)


def op_alerts_test_email(agent, args):
    try:
        agent.send_test_email()
    except MailError as exc:
        return {"ok": False, "code": exc.code, "message": exc.message}
    return {"ok": True}


def op_drain(agent, args):
    force = _arg(args, "force", bool, required=False, default=False)
    return agent.drain(force=force)


OPS: Dict[str, Callable[[Any, Dict[str, Any]], Any]] = {
    "status": op_status,
    "jobs.list": op_jobs_list,
    "jobs.get": op_jobs_get,
    "jobs.create": op_jobs_create,
    "jobs.update": op_jobs_update,
    "jobs.delete": op_jobs_delete,
    "jobs.run_now": op_jobs_run_now,
    "jobs.pause": op_jobs_pause,
    "jobs.resume": op_jobs_resume,
    "jobs.preview": op_jobs_preview,
    "runs.list": op_runs_list,
    "runs.output": op_runs_output,
    "runs.cancel": op_runs_cancel,
    "alerts.get": op_alerts_get,
    "alerts.configure": op_alerts_configure,
    "alerts.test_email": op_alerts_test_email,
    "drain": op_drain,
}


def error(code: str, message: str) -> Dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}}


def handle(agent, request: Any) -> Dict[str, Any]:
    try:
        if not isinstance(request, dict):
            raise _bad("请求必须是 JSON 对象")
        version = request.get("protocol_version")
        if not _is_int(version):
            raise _bad("缺少 protocol_version")
        if version != PROTOCOL_VERSION:
            raise ProtocolError("protocol_mismatch",
                                "协议版本不兼容：Agent 使用 %d，请求为 %d，请升级"
                                % (PROTOCOL_VERSION, version))
        op = request.get("op")
        if not isinstance(op, str) or op not in OPS:
            raise _bad("未知操作：%s" % (op if isinstance(op, str) else "?"))
        args = request.get("args", {})
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise _bad("args 必须是对象")
        return {"ok": True, "result": OPS[op](agent, args)}
    except ProtocolError as exc:
        return error(exc.code, exc.message)
    except StoreError as exc:
        code = exc.code if exc.code in ERROR_CODES else "invalid"
        return error(code, exc.message)
    except Exception:
        print("panellab-agent: request failed\n" + traceback.format_exc(), file=sys.stderr)
        return error("internal", INTERNAL_MESSAGE)


__all__ = ["handle", "ProtocolError", "OPS", "PROTOCOL_VERSION", "__version__"]
