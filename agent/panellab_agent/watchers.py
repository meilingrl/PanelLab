"""Resource, SSH-failure and heartbeat checks.

Watchers only compute results; the daemon feeds them to ``AlertBook.observe``.
Everything touching the system (/proc, logs, network) is injectable.
"""
from __future__ import annotations

import os
import re
import subprocess
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

from .clock import Clock

DEFAULTS = {
    "disk_percent": 90,
    "disk_recover_percent": 85,
    "cpu_percent": 95,
    "mem_percent": 95,
    "sustain_minutes": 10,
    "recover_below_percent": 90,
    "recover_minutes": 5,
    "ssh_failures": 30,
    "ssh_window_minutes": 10,
    "ssh_enabled": True,
}

SAMPLE_GAP_RESET_S = 3 * 30


def _cfg(cfg: Optional[dict], key: str):
    if cfg and cfg.get(key) is not None:
        return cfg[key]
    return DEFAULTS[key]


RECOVER_GAP = 5


def effective_recover(cfg: Optional[dict], trigger_key: str) -> float:
    """Recovery level for a trigger: min(configured or default, trigger - 5).

    Keeps a lowered trigger from sitting above the recovery level, which
    would fire and recover on every check.
    """
    recover_key = "disk_recover_percent" if trigger_key == "disk_percent" \
        else "recover_below_percent"
    return min(float(_cfg(cfg, recover_key)), float(_cfg(cfg, trigger_key)) - RECOVER_GAP)


# --------------------------------------------------------------------- disk

PSEUDO_FS = {
    "tmpfs", "devtmpfs", "overlay", "squashfs", "proc", "sysfs", "nsfs",
    "ramfs", "autofs", "devpts", "mqueue", "hugetlbfs", "debugfs", "tracefs",
    "securityfs", "pstore", "bpf", "configfs", "fusectl", "binfmt_misc",
    "efivarfs",
}

_OCTAL = re.compile(r"\\([0-7]{3})")


def _unescape(s: str) -> str:
    return _OCTAL.sub(lambda m: chr(int(m.group(1), 8)), s)


def _default_read_mounts() -> str:
    with open("/proc/self/mounts", "r", encoding="utf-8", errors="replace") as f:
        return f.read()


class DiskWatch:
    def __init__(self, read_mounts: Callable[[], str] = _default_read_mounts,
                 statvfs: Callable[[str], object] = os.statvfs) -> None:
        self._read_mounts = read_mounts
        self._statvfs = statvfs
        self._firing: Dict[str, bool] = {}

    def _mounts(self) -> List[str]:
        by_dev: Dict[str, str] = {}
        for line in self._read_mounts().splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue
            dev, path, fstype = parts[0], _unescape(parts[1]), parts[2]
            if fstype in PSEUDO_FS or fstype.startswith("cgroup"):
                continue
            prev = by_dev.get(dev)
            if prev is None or (path == "/" and prev != "/"):
                by_dev[dev] = path
        return list(by_dev.values())

    def check(self, cfg: Optional[dict] = None) -> List[Tuple[str, bool, str, str]]:
        high = float(_cfg(cfg, "disk_percent"))
        low = effective_recover(cfg, "disk_percent")
        out = []
        for path in self._mounts():
            try:
                st = self._statvfs(path)
            except OSError:
                continue
            if not st.f_blocks:
                continue
            pct = 100.0 * (st.f_blocks - st.f_bavail) / st.f_blocks
            was = self._firing.get(path, False)
            now = was
            if not was and pct >= high:
                now = True
            elif was and pct < low:
                now = False
            if now != was:
                self._firing[path] = now
                subject = "磁盘空间不足：%s 已用 %d%%" % (path, round(pct))
                body = "挂载点 %s 当前已用 %.1f%%（告警阈值 %g%%，恢复阈值 %g%%）。" % (
                    path, pct, high, low)
                out.append(("disk:" + path, now, subject, body))
        return out


# ------------------------------------------------------------ CPU / memory

def _parse_cpu(line: str):
    parts = line.split()
    if not parts or parts[0] != "cpu":
        return None
    try:
        v = [int(x) for x in parts[1:]]
    except ValueError:
        return None
    v += [0] * (5 - len(v))
    idle = v[3] + v[4]  # idle + iowait
    return sum(v), idle


def read_cpu_percent(prev: str, cur: str) -> Optional[float]:
    a, b = _parse_cpu(prev), _parse_cpu(cur)
    if a is None or b is None:
        return None
    total = b[0] - a[0]
    if total <= 0:
        return None
    busy = total - (b[1] - a[1])
    return max(0.0, min(100.0, 100.0 * busy / total))


def read_mem_percent(meminfo: str) -> Optional[float]:
    vals = {}
    for line in meminfo.splitlines():
        m = re.match(r"(MemTotal|MemAvailable):\s+(\d+)", line)
        if m:
            vals[m.group(1)] = int(m.group(2))
    total, avail = vals.get("MemTotal"), vals.get("MemAvailable")
    if not total or avail is None:
        return None
    return 100.0 * (1 - avail / total)


class SustainedWatch:
    """Fires after ``sustain_minutes`` continuously at/above the threshold and
    recovers after ``recover_minutes`` continuously below the effective
    recovery level (see ``effective_recover``).

    ``name`` ("cpu" or "mem") selects the ``<name>_percent`` threshold. A gap
    between samples longer than 90 s resets both timers.
    """

    def __init__(self, name: str, clock: Clock) -> None:
        self.name = name
        self._clock = clock
        self.firing = False
        self._above_since: Optional[float] = None
        self._below_since: Optional[float] = None
        self._last: Optional[float] = None

    def sample(self, percent: float, cfg: Optional[dict] = None) -> Optional[bool]:
        now = self._clock.monotonic()
        if self._last is not None and now - self._last > SAMPLE_GAP_RESET_S:
            self._above_since = None
            self._below_since = None
        self._last = now
        thr = float(_cfg(cfg, self.name + "_percent"))
        if not self.firing:
            if percent >= thr:
                if self._above_since is None:
                    self._above_since = now
                if now - self._above_since >= float(_cfg(cfg, "sustain_minutes")) * 60:
                    self.firing = True
                    self._above_since = None
                    return True
            else:
                self._above_since = None
            return None
        if percent < effective_recover(cfg, self.name + "_percent"):
            if self._below_since is None:
                self._below_since = now
            if now - self._below_since >= float(_cfg(cfg, "recover_minutes")) * 60:
                self.firing = False
                self._below_since = None
                return False
        else:
            self._below_since = None
        return None


# ---------------------------------------------------------------------- SSH

_IP = r"(?P<ip>[0-9A-Fa-f:]*[:.][0-9A-Fa-f:.]*)"
SSH_PATTERNS = [
    re.compile(r"Failed password for (?:invalid user )?\S+ from " + _IP),
    re.compile(r"Invalid user \S+ from " + _IP),
    re.compile(r"authentication failure;.*rhost=" + _IP),
    re.compile(r"Connection closed by authenticating user \S+ " + _IP),
]

_ISO_TS = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)")
_SYSLOG_TS = re.compile(r"^([A-Z][a-z]{2})\s+(\d{1,2}) (\d{2}):(\d{2}):(\d{2})")
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
           "Oct", "Nov", "Dec"]


def _line_time(line: str, now: Optional[datetime] = None) -> Optional[datetime]:
    m = _ISO_TS.match(line)
    if m:
        s = m.group(1)
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        s = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", s)
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.astimezone()
    m = _SYSLOG_TS.match(line)
    if m and m.group(1) in _MONTHS:
        now = now or datetime.now().astimezone()
        try:
            dt = datetime(now.year, _MONTHS.index(m.group(1)) + 1, int(m.group(2)),
                          int(m.group(3)), int(m.group(4)), int(m.group(5)))
        except ValueError:
            return None
        dt = dt.astimezone()  # naive -> local time
        if dt > now + timedelta(days=1):  # Dec lines read in January
            try:
                dt = dt.replace(year=dt.year - 1)
            except ValueError:
                return None
        return dt
    return None


def _filter_since(lines: List[str], since: datetime) -> List[str]:
    out = []
    for line in lines:
        t = _line_time(line)
        if t is None or t >= since:
            out.append(line)
    return out


def default_ssh_lines(since: datetime) -> List[str]:
    since_s = since.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    try:
        r = subprocess.run(
            ["journalctl", "-u", "ssh", "-u", "sshd", "--since", since_s,
             "-o", "short-iso", "--no-pager"],
            capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            return _filter_since(r.stdout.splitlines(), since)
    except Exception:
        pass
    try:
        with open("/var/log/auth.log", "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 2 * 1024 * 1024))
            data = f.read().decode("utf-8", errors="replace")
        return _filter_since(data.splitlines(), since)
    except Exception:
        return []


class SshWatch:
    """Counts failed SSH logins per source IP in a sliding window.

    Each matching log line counts once. One login attempt by a non-existent
    user often logs both ``Invalid user`` and ``Failed password``, so counts can
    double; that is acceptable for a threshold alarm.
    """

    def __init__(self, read_lines: Callable[[datetime], List[str]] = default_ssh_lines) -> None:
        self._read_lines = read_lines

    def check(self, now: datetime, cfg: Optional[dict] = None) -> Tuple[bool, str, str]:
        minutes = int(_cfg(cfg, "ssh_window_minutes"))
        if not _cfg(cfg, "ssh_enabled"):
            return False, "SSH 登录失败监控已关闭", ""
        threshold = int(_cfg(cfg, "ssh_failures"))
        counts: Counter = Counter()
        for line in self._read_lines(now - timedelta(minutes=minutes)):
            for pat in SSH_PATTERNS:
                m = pat.search(line)
                if m:
                    counts[m.group("ip")] += 1
                    break
        total = sum(counts.values())
        subject = "SSH 登录失败激增：%d 分钟内 %d 次" % (minutes, total)
        rows = ["%s  %d" % (ip, n) for ip, n in counts.most_common(5)]
        body = "来源 IP 与失败次数（最多 5 个）：\n" + "\n".join(rows) if rows else ""
        return total >= threshold, subject, body


# ---------------------------------------------------------------- heartbeat

HEARTBEAT_INTERVAL_S = 300
HEARTBEAT_ERROR = "心跳请求失败"


class Heartbeat:
    def __init__(self, url_getter: Callable[[], Optional[str]], clock: Clock,
                 opener=urllib.request.urlopen) -> None:
        self._url_getter = url_getter
        self._clock = clock
        self._opener = opener
        self._last_mono: Optional[float] = None
        self._result = {"at": None, "ok": None, "error": None}

    def last_result(self) -> dict:
        return dict(self._result)

    def tick(self) -> None:
        try:
            url = self._url_getter()
            if not url or not str(url).startswith("https://"):
                return
            now = self._clock.monotonic()
            if self._last_mono is not None and now - self._last_mono < HEARTBEAT_INTERVAL_S:
                return
            self._last_mono = now
            at = self._clock.now().isoformat()
            try:
                req = urllib.request.Request(url, method="GET",
                                             headers={"User-Agent": "panellab-agent"})
                resp = self._opener(req, timeout=10)
                try:
                    status = getattr(resp, "status", 200)
                finally:
                    close = getattr(resp, "close", None)
                    if close:
                        close()
                ok = status is None or int(status) < 400
                self._result = {"at": at, "ok": ok,
                                "error": None if ok else HEARTBEAT_ERROR}
            except Exception:
                self._result = {"at": at, "ok": False, "error": HEARTBEAT_ERROR}
        except Exception:
            pass
