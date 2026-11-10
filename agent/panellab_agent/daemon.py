"""PanelLab Agent daemon: wires store, scheduler, runner, alerts and watchers
together, ticks once a second and serves the control protocol on a Unix socket.

    python3 -m panellab_agent.daemon --config /etc/panellab-agent/agent.json \
        --data-dir /var/lib/panellab-agent --socket /run/panellab-agent/agent.sock
"""
from __future__ import annotations

import argparse
import copy
import errno
import fcntl
import json
import os
import queue
import signal
import socket
import socketserver
import stat
import sys
import tempfile
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from . import PROTOCOL_VERSION, __version__, cron, protocol
from .alerts import AlertBook, MailError, Mailer
from .clock import Clock
from .runner import Runner
from .scheduler import plan_tick
from .secret_store import Secrets
from .store import DEFAULT_ALERT, Store
from .watchers import (DEFAULTS as WATCH_DEFAULTS, DiskWatch, Heartbeat, SshWatch,
                       SustainedWatch, default_ssh_lines, effective_recover,
                       read_cpu_percent, read_mem_percent)

DOWNTIME_THRESHOLD = timedelta(minutes=5)
TAIL_LINES = 20
TAIL_BYTES = 64 * 1024
CPU_MEM_INTERVAL_S = 30
DISK_SSH_INTERVAL_S = 60
HEARTBEAT_LOOP_S = 30
PRUNE_INTERVAL_S = 3600
MAX_REQUEST = 1024 * 1024
SOCKET_TIMEOUT_S = 30
DRAIN_GRACE_S = 60
MAIL_QUEUE_SIZE = 1000
MAIL_FLUSH_TIMEOUT_S = 20
ALREADY_RUNNING = "另一个 Agent 正在运行"


class AgentAlreadyRunning(RuntimeError):
    def __init__(self) -> None:
        super().__init__(ALREADY_RUNNING)


def schedule_alert_key(job_id: str) -> str:
    """Kept apart from job:<id> so schedule errors don't flap with run results."""
    return "job:%s:schedule" % job_id


def _acquire_lock(data_dir: str) -> int:
    """Exclusive flock on <data_dir>/agent.lock, held until the fd is closed."""
    fd = os.open(os.path.join(data_dir, "agent.lock"), os.O_RDWR | os.O_CREAT, 0o640)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(fd)
        if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
            raise AgentAlreadyRunning()
        raise
    return fd


def check_socket_free(path: str) -> None:
    """Raise AgentAlreadyRunning if something is listening on `path`; unlink a stale socket."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(st.st_mode):
        raise RuntimeError("refusing to replace non-socket file %s" % path)
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(2)
    try:
        probe.connect(path)
    except OSError:
        live = False  # refused (or otherwise dead): a stale leftover
    else:
        live = True
    finally:
        probe.close()
    if live:
        raise AgentAlreadyRunning()
    os.unlink(path)


def _log(message: str) -> None:
    print("panellab-agent: " + message, file=sys.stderr, flush=True)


def _parse_ts(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ----------------------------------------------------------- server timezone

def server_timezone(environ: Optional[Dict[str, str]] = None,
                    etc_timezone: str = "/etc/timezone",
                    localtime: str = "/etc/localtime") -> str:
    """TZ env, else /etc/timezone, else the /etc/localtime link target, else UTC."""
    environ = os.environ if environ is None else environ

    def from_env():
        return (environ.get("TZ") or "").lstrip(":")

    def from_file():
        with open(etc_timezone, "r", encoding="utf-8") as f:
            return f.readline().strip()

    def from_link():
        target = os.readlink(localtime)
        return target.split("zoneinfo/", 1)[1] if "zoneinfo/" in target else ""

    for source in (from_env, from_file, from_link):
        try:
            name = source()
            if name:
                cron.validate_tz(name)
                return name
        except (OSError, ValueError, IndexError):
            continue
    return "UTC"


# --------------------------------------------------------------- agent.json

def default_alert_config() -> Dict[str, Any]:
    return {"smtp": None, "thresholds": {}, "heartbeat_url": None,
            "ssh_enabled": WATCH_DEFAULTS["ssh_enabled"]}


def load_config(path: str) -> Dict[str, Any]:
    """The `alerts` section of agent.json; defaults when missing or corrupt."""
    cfg = default_alert_config()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return cfg
    except (OSError, ValueError):
        _log("agent.json is unreadable or corrupt; using default alert settings")
        return cfg
    alerts = data.get("alerts") if isinstance(data, dict) else None
    if not isinstance(alerts, dict):
        _log("agent.json has no valid alerts section; using default alert settings")
        return cfg
    for key in cfg:
        if key in alerts:
            cfg[key] = alerts[key]
    if isinstance(cfg["smtp"], dict):
        cfg["smtp"] = {k: v for k, v in cfg["smtp"].items() if k != "password"}
    elif cfg["smtp"] is not None:
        cfg["smtp"] = None
    if not isinstance(cfg["thresholds"], dict):
        cfg["thresholds"] = {}
    return cfg


def write_config(path: str, alerts: Dict[str, Any]) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".agent-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            os.fchmod(f.fileno(), 0o640)
            json.dump({"alerts": alerts}, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --------------------------------------------------------------- mail queue

class QueuedMailer:
    """Same interface as Mailer, but send() only enqueues.

    One background thread delivers mail, so a hanging SMTP server never blocks
    the tick or a request. Failures are recorded by the inner mailer.
    """

    def __init__(self, inner: Any, maxsize: int = MAIL_QUEUE_SIZE) -> None:
        self.inner = inner
        self._q: "queue.Queue[Any]" = queue.Queue(maxsize)
        self._cond = threading.Condition()
        self._pending = 0
        self._thread = threading.Thread(target=self._run, daemon=True, name="mail-queue")
        self._thread.start()

    def send(self, subject: str, body: str) -> None:
        with self._cond:
            self._pending += 1
        try:
            self._q.put_nowait((subject, body))
        except queue.Full:
            self._done()
            _log("alert mail queue is full; mail dropped")

    def last_result(self) -> Dict[str, Any]:
        return self.inner.last_result()

    def _done(self) -> None:
        with self._cond:
            self._pending -= 1
            self._cond.notify_all()

    def _run(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            try:
                self.inner.send(*item)
            except MailError:
                pass  # recorded in inner.last_result()
            except Exception:
                _log("alert mail failed unexpectedly")
            finally:
                self._done()

    def wait_idle(self, timeout: float) -> bool:
        with self._cond:
            return self._cond.wait_for(lambda: self._pending == 0, timeout)

    def close(self, timeout: float = MAIL_FLUSH_TIMEOUT_S) -> bool:
        """Wait up to `timeout` for queued mail to go out, then stop the thread.

        Returns True when the queue was fully drained.
        """
        idle = self.wait_idle(timeout)
        try:
            self._q.put_nowait(None)
        except queue.Full:
            pass
        if not idle:
            _log("exiting with unsent alert mail")
        return idle


# -------------------------------------------------------------- system reads

def _read_first_line(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.readline()
    except OSError:
        return None


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def default_readers() -> Dict[str, Any]:
    return {
        "proc_stat": lambda: _read_first_line("/proc/stat"),
        "meminfo": lambda: _read_text("/proc/meminfo"),
        "mounts": lambda: _read_text("/proc/self/mounts") or "",
        "statvfs": os.statvfs,
        "ssh_lines": default_ssh_lines,
        "opener": None,
    }


# --------------------------------------------------------------------- agent

class Agent:
    def __init__(self, config_path: str, data_dir: str, clock: Optional[Clock] = None, *,
                 mailer: Any = None, hostname: Optional[str] = None,
                 server_tz: Optional[str] = None,
                 readers: Optional[Dict[str, Any]] = None) -> None:
        self.clock = clock or Clock()
        self.config_path = config_path
        self.closed = False
        os.makedirs(data_dir, exist_ok=True)
        # Before the store or recovery: a second instance must not touch live runs.
        self._lock_fd = _acquire_lock(data_dir)
        self._lock = threading.RLock()
        self._config = load_config(config_path)
        self.hostname = hostname or socket.gethostname()
        self.server_tz = server_tz or server_timezone()
        self.started_at = self.clock.now().isoformat()
        self._draining = False
        self.store = Store(os.path.join(data_dir, "agent.db"), self.clock)
        self.secrets = Secrets(os.path.join(os.path.dirname(os.path.abspath(config_path)),
                                            "secrets.json"))
        self.mailer = mailer or Mailer(self.alert_config, self.secrets.smtp_password, self.clock)
        self.mail_queue = QueuedMailer(self.mailer)
        self.alerts = AlertBook(self.store, self.clock, self.mail_queue, self.hostname)
        self.output_dir = os.path.join(data_dir, "output")
        self.runner = Runner(self.store, self.secrets, self.clock, self.output_dir,
                             self.on_finish)
        r = default_readers()
        r.update(readers or {})
        self._readers = r
        self.disk_watch = DiskWatch(r["mounts"], r["statvfs"])
        self.cpu_watch = SustainedWatch("cpu", self.clock)
        self.mem_watch = SustainedWatch("mem", self.clock)
        self.ssh_watch = SshWatch(r["ssh_lines"])
        hb_kw = {"opener": r["opener"]} if r.get("opener") else {}
        self.heartbeat = Heartbeat(lambda: self.alert_config().get("heartbeat_url"),
                                   self.clock, **hb_kw)
        self._cpu_prev: Optional[str] = None
        self._last_phase: Dict[str, float] = {}
        self._pruned_at: Dict[str, float] = {}
        self._reported_errors: set = set()
        self._stop = threading.Event()
        self._last_tick = self.clock.now()
        self._recover()

    # ---- config -----------------------------------------------------------

    def alert_config(self) -> Dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._config)

    def update_alert_config(self, changes: Dict[str, Any]) -> None:
        with self._lock:
            new = copy.deepcopy(self._config)
            new.update(changes)
            write_config(self.config_path, new)
            self._config = load_config(self.config_path)

    def _watch_cfg(self) -> Dict[str, Any]:
        cfg = self.alert_config()
        out = dict(cfg.get("thresholds") or {})
        out["ssh_enabled"] = bool(cfg.get("ssh_enabled", True))
        return out

    def alerts_configured(self) -> bool:
        smtp = self.alert_config().get("smtp") or {}
        return bool(smtp.get("host") and smtp.get("to"))

    # ---- startup recovery ---------------------------------------------------

    def _recover(self) -> None:
        now = self.clock.now()
        for run in self.store.running_runs():
            try:
                path = os.path.join(self.output_dir, "%d.log" % run["id"])
                try:
                    size = os.path.getsize(path)
                except OSError:
                    size = 0
                self.store.finish_run(run["id"], "agent_lost", None, size, False)
                job = self.store.get_job(run["job_id"]) or {
                    "id": run["job_id"], "name": run["job_id"], "alert": dict(DEFAULT_ALERT)}
                self.on_finish(job, self.store.get_run(run["id"]))
            except Exception:
                _log("could not mark run %s as agent_lost:\n%s"
                     % (run["id"], traceback.format_exc()))
        last_alive = _parse_ts(self.store.get_meta("last_alive"))
        if last_alive is not None and now - last_alive > DOWNTIME_THRESHOLD:
            zone = ZoneInfo(self.server_tz)
            self.alerts.notify("Agent 曾停止运行", "从 %s 到 %s（本地时间 %s 到 %s）" % (
                last_alive.isoformat(), now.isoformat(),
                last_alive.astimezone(zone).strftime("%Y-%m-%d %H:%M:%S"),
                now.astimezone(zone).strftime("%Y-%m-%d %H:%M:%S %Z")))
        # Due times are judged from the last completed scheduling pass; that
        # equals last_alive except while the agent was draining.
        since = _parse_ts(self.store.get_meta("last_scheduled")) or last_alive or now
        self._last_tick = min(since, now)
        self.store.set_meta("last_alive", now.isoformat())

    # ---- job alerts ---------------------------------------------------------

    def _tail(self, run: Dict[str, Any]) -> str:
        size = int(run.get("output_bytes") or 0)
        offset = max(0, size - TAIL_BYTES)
        data = self.runner.read_output(run["id"], offset, TAIL_BYTES)["data"]
        lines = data.splitlines()
        if offset > 0 and lines:
            lines = lines[1:]  # first line is probably partial
        return "\n".join(lines[-TAIL_LINES:])

    def _run_body(self, job: Dict[str, Any], run: Dict[str, Any], with_output: bool) -> str:
        status_text = {"failed": "失败", "timed_out": "超时",
                       "agent_lost": "Agent 重启，运行被中断", "succeeded": "成功"}
        parts = ["作业：%s（%s）" % (job.get("name") or job["id"], job["id"]),
                 "运行：#%s，触发方式：%s，第 %s 次尝试" % (
                     run.get("id"), run.get("trigger"), run.get("attempt")),
                 "状态：%s" % status_text.get(run.get("status"), run.get("status"))]
        if run.get("scheduled_for"):
            parts.append("计划时间（UTC）：%s" % run["scheduled_for"])
        if run.get("exit_code") is not None:
            parts.append("退出码：%s" % run["exit_code"])
        if with_output:
            try:
                tail = self._tail(run)
            except Exception:
                tail = ""
            if tail:
                parts.append("")
                parts.append("最近 %d 行输出：" % TAIL_LINES)
                parts.append(tail)
        return "\n".join(parts)

    def on_finish(self, job: Dict[str, Any], run: Optional[Dict[str, Any]]) -> None:
        if not run or self.store.get_job(job["id"]) is None:
            return  # deleted jobs raise no alerts
        status = run.get("status")
        key = "job:" + job["id"]
        name = job.get("name") or job["id"]
        alert = job.get("alert") or DEFAULT_ALERT
        if status in ("failed", "timed_out", "agent_lost"):
            subject = ("作业超时：%s" if status == "timed_out" else "作业失败：%s") % name
            body = self._run_body(job, run, bool(alert.get("include_output")))
            self.alerts.observe(key, True, subject, body,
                                notify_firing=bool(alert.get("on_failure")))
        elif status == "succeeded":
            self.alerts.observe(key, False, "作业恢复正常：%s" % name,
                                self._run_body(job, run, False),
                                notify_recovery=bool(alert.get("on_recovery")))

    # ---- control ------------------------------------------------------------

    @property
    def draining(self) -> bool:
        with self._lock:
            return self._draining

    def run_now(self, job: Dict[str, Any]) -> int:
        with self._lock:
            if self._draining:
                raise protocol.ProtocolError("draining", "Agent 正在排空，不再启动新运行")
            if job["id"] in self.runner.running_job_ids():
                raise protocol.ProtocolError("conflict", "作业正在运行或等待重试")
            return self.runner.start(job, "manual", None)

    def drain(self, force: bool = False) -> Dict[str, Any]:
        with self._lock:
            self._draining = True
        self.runner.drop_retries()
        running = self.store.running_runs()
        if force:
            for run in running:
                self.runner.cancel(run["id"])
        timeouts = []
        for run in running:
            job = self.store.get_job(run["job_id"])
            timeouts.append(int(job["timeout_seconds"]) if job else 3600)
        return {"draining": True, "active_count": self.runner.active_count(),
                "wait_seconds": max(timeouts, default=0) + DRAIN_GRACE_S}

    def send_test_email(self) -> None:
        """Bypasses the queue so the caller sees the real result (raises MailError)."""
        now = self.clock.now()
        self.mailer.send("[PanelLab · %s] 测试邮件" % self.hostname.replace("\n", " "),
                         "这是一封来自 PanelLab Agent 的测试邮件。\n主机：%s\n时间（UTC）：%s\n"
                         % (self.hostname, now.isoformat()))

    def status(self) -> Dict[str, Any]:
        return {
            "version": __version__,
            "protocol_version": PROTOCOL_VERSION,
            "started_at": self.started_at,
            "hostname": self.hostname,
            "server_time": self.clock.now().isoformat(),
            "timezone": self.server_tz,
            "draining": self.draining,
            "job_count": len(self.store.list_jobs()),
            "running": [{"run_id": r["id"], "job_id": r["job_id"], "started_at": r["started_at"]}
                        for r in self.store.running_runs()],
            "firing": [{"key": a["key"], "since": a["since"], "message": a["message"]}
                       for a in self.store.firing_alerts()],
            "mail": self.mail_queue.last_result(),
            "alerts_configured": self.alerts_configured(),
        }

    # ---- tick ---------------------------------------------------------------

    def _phase(self, name: str, fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception:
            _log("tick phase %s failed:\n%s" % (name, traceback.format_exc()))

    def _due(self, name: str, interval: float) -> bool:
        mono = self.clock.monotonic()
        last = self._last_phase.get(name)
        if last is not None and mono - last < interval:
            return False
        self._last_phase[name] = mono
        return True

    def tick(self) -> None:
        now = self.clock.now()
        self._phase("schedule", lambda: self._schedule(now))
        self._phase("runner", self._poll_runner)
        if self._due("cpu_mem", CPU_MEM_INTERVAL_S):
            self._phase("cpu", self._check_cpu)
            self._phase("mem", self._check_mem)
        if self._due("disk_ssh", DISK_SSH_INTERVAL_S):
            self._phase("disk", self._check_disk)
            self._phase("ssh", lambda: self._check_ssh(now))
        self._phase("prune", self._prune)
        self._phase("last_alive", lambda: self.store.set_meta("last_alive", now.isoformat()))

    def _schedule(self, now: datetime) -> None:
        if self.draining:
            return  # last_scheduled stays put so a restart catches these up
        jobs = self.store.list_jobs()
        by_id = {j["id"]: j for j in jobs}
        with self._lock:
            since = self._last_tick
        dec = plan_tick(jobs, {j["id"]: since for j in jobs}, now,
                        self.runner.running_job_ids())
        current = set()
        for jid, message in dec.errors:
            current.add((jid, message))
            if (jid, message) not in self._reported_errors:
                _log("job %s has an invalid schedule: %s" % (jid, message))
            self.alerts.observe(schedule_alert_key(jid), True, "作业计划无效：%s" % jid, message)
        self._reported_errors = current
        bad = {jid for jid, _ in dec.errors}
        for alert in self.store.firing_alerts():
            key = alert["key"]
            if key.startswith("job:") and key.endswith(":schedule") \
                    and key[4:-len(":schedule")] in by_id and key[4:-len(":schedule")] not in bad:
                self.clear_schedule_alert(key[4:-len(":schedule")])
        for jid, due in dec.missed:
            job = by_id[jid]
            self.store.record_missed(jid, due)
            alert = job.get("alert") or DEFAULT_ALERT
            self.alerts.observe("job:%s" % jid, True, "作业错过计划：%s" % job["name"],
                                "作业：%s（%s）\n计划时间（UTC）：%s\n超出补跑窗口，未运行。"
                                % (job["name"], jid, due.isoformat()),
                                notify_firing=bool(alert.get("on_failure")))
        for jid, due in dec.skipped_overlap:
            self.store.record_skipped(jid, due)
        for start in dec.start:
            job = by_id[start.job_id]
            try:
                with self._lock:
                    if self._draining:
                        break
                    if start.job_id in self.runner.running_job_ids():
                        self.store.record_skipped(start.job_id, start.scheduled_for)
                        continue
                    if start.trigger == "catch_up":
                        self.alerts.notify(
                            "补跑：%s 迟到 %d 分钟" % (job["name"], start.late_minutes),
                            "作业：%s（%s）\n计划时间（UTC）：%s\n迟到 %d 分钟后补跑。"
                            % (job["name"], job["id"], start.scheduled_for.isoformat(),
                               start.late_minutes))
                    self.runner.start(job, start.trigger, start.scheduled_for)
            except Exception:
                _log("could not start job %s:\n%s" % (start.job_id, traceback.format_exc()))
        with self._lock:
            self._last_tick = now
        self.store.set_meta("last_scheduled", now.isoformat())

    def _poll_runner(self) -> None:
        if self.draining:
            self.runner.drop_retries()
        self.runner.poll()
        if self.draining:
            self.runner.drop_retries()

    def _check_cpu(self) -> None:
        cur = self._readers["proc_stat"]()
        if not cur:
            return
        prev, self._cpu_prev = self._cpu_prev, cur
        pct = read_cpu_percent(prev, cur) if prev else None
        if pct is not None:
            self._sustained(self.cpu_watch, "cpu", "CPU", pct)

    def _check_mem(self) -> None:
        text = self._readers["meminfo"]()
        if not text:
            return
        pct = read_mem_percent(text)
        if pct is not None:
            self._sustained(self.mem_watch, "mem", "内存", pct)

    def _sustained(self, watch: SustainedWatch, key: str, label: str, pct: float) -> None:
        cfg = self._watch_cfg()
        changed = watch.sample(pct, cfg)
        if changed is None:
            return
        th = dict(WATCH_DEFAULTS)
        th.update(cfg)
        subject = "%s 使用率持续偏高：%d%%" % (label, round(pct))
        body = "%s 使用率当前为 %.1f%%（告警阈值 %g%%，持续 %g 分钟；低于 %g%% 持续 %g 分钟恢复）。" % (
            label, pct, th[key + "_percent"], th["sustain_minutes"],
            effective_recover(cfg, key + "_percent"), th["recover_minutes"])
        self.alerts.observe(key, changed, subject, body)

    def _check_disk(self) -> None:
        for key, firing, subject, body in self.disk_watch.check(self._watch_cfg()):
            self.alerts.observe(key, firing, subject, body)

    def _check_ssh(self, now: datetime) -> None:
        firing, subject, body = self.ssh_watch.check(now, self._watch_cfg())
        self.alerts.observe("ssh", firing, subject, body)

    def _prune(self) -> None:
        mono = self.clock.monotonic()
        for job in self.store.list_jobs():
            last = self._pruned_at.get(job["id"])
            if last is not None and mono - last < PRUNE_INTERVAL_S:
                continue
            self._pruned_at[job["id"]] = mono
            removed = self.store.prune(job["id"])
            if removed:
                self.runner.delete_output(removed)

    # ---- lifecycle ----------------------------------------------------------

    def start_background(self) -> None:
        """Heartbeat on its own thread, so a slow URL never delays the tick."""
        def loop():
            while not self._stop.is_set():
                self.heartbeat.tick()
                self._stop.wait(HEARTBEAT_LOOP_S)
        threading.Thread(target=loop, daemon=True, name="heartbeat").start()

    def clear_schedule_alert(self, job_id: str) -> None:
        self.alerts.observe(schedule_alert_key(job_id), False, "作业计划已恢复：%s" % job_id, "",
                            notify_recovery=False)

    def close(self, mail_timeout: float = MAIL_FLUSH_TIMEOUT_S) -> None:
        if self.closed:
            return
        self.closed = True
        self._stop.set()
        self.mail_queue.close(mail_timeout)
        self.store.close()
        os.close(self._lock_fd)  # releases the flock


# -------------------------------------------------------------------- socket

class _Handler(socketserver.StreamRequestHandler):
    timeout = SOCKET_TIMEOUT_S

    def handle(self) -> None:
        try:
            line = self.rfile.readline(MAX_REQUEST + 1)
        except OSError:  # includes socket.timeout
            return
        if not line:
            return
        if len(line) > MAX_REQUEST and not line.endswith(b"\n"):
            resp = protocol.error("bad_request", "请求超过 1 MiB")
        else:
            try:
                request = json.loads(line.decode("utf-8"))
            except Exception:  # bad UTF-8, bad JSON, absurd nesting
                resp = protocol.error("bad_request", "请求不是有效的 JSON")
            else:
                resp = protocol.handle(self.server.agent, request)  # type: ignore[attr-defined]
        try:
            self.wfile.write((json.dumps(resp, ensure_ascii=False) + "\n").encode("utf-8"))
            self.wfile.flush()
        except OSError:
            pass


class _Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, path: str, agent: Agent) -> None:
        self.agent = agent
        super().__init__(path, _Handler)


def make_server(agent: Agent, path: str) -> _Server:
    """Bind the control socket (replacing a stale one) with mode 0660.

    Raises AgentAlreadyRunning when another process is listening on `path`.
    """
    check_socket_free(path)
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    old = os.umask(0o117)
    try:
        server = _Server(path, agent)
    finally:
        os.umask(old)
    os.chmod(path, 0o660)
    return server


# ---------------------------------------------------------------------- main

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="panellab-agent")
    parser.add_argument("--config", default="/etc/panellab-agent/agent.json")
    parser.add_argument("--data-dir", default="/var/lib/panellab-agent")
    parser.add_argument("--socket", default="/run/panellab-agent/agent.sock")
    parser.add_argument("--foreground", action="store_true",
                        help="run in the foreground (the agent never forks; for systemd)")
    args = parser.parse_args(argv)

    try:
        check_socket_free(args.socket)  # before recovery touches any runs
        agent = Agent(args.config, args.data_dir, Clock())
    except AgentAlreadyRunning:
        _log(ALREADY_RUNNING)
        return 1
    try:
        server = make_server(agent, args.socket)
    except AgentAlreadyRunning:
        _log(ALREADY_RUNNING)
        agent.close()
        return 1
    stop = threading.Event()

    def on_signal(signum, frame):  # only set a flag: locks may be held right now
        stop.set()
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    threading.Thread(target=server.serve_forever, daemon=True, name="control").start()
    agent.start_background()
    _log("version %s listening on %s" % (__version__, args.socket))
    draining_for_exit = False
    try:
        while True:
            began = time.monotonic()
            if stop.is_set() and not draining_for_exit:
                draining_for_exit = True
                agent.drain()
                _log("SIGTERM received; draining before exit")
            agent.tick()
            if draining_for_exit and agent.runner.active_count() == 0:
                break
            time.sleep(max(0.05, 1.0 - (time.monotonic() - began)))
    finally:
        server.shutdown()
        server.server_close()
        try:
            os.unlink(args.socket)
        except OSError:
            pass
        agent.close()
    _log("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
