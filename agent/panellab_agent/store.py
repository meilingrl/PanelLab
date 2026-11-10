"""SQLite store for job definitions, run history, alert state and metadata.

Secrets never live here (see secret_store.py). One connection is shared by all
threads and guarded by a lock; the database runs in WAL mode.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .clock import Clock
from .cron import CronError, parse, validate_tz

RUN_STATUSES = ("running", "succeeded", "failed", "timed_out", "skipped_overlap",
                "missed", "agent_lost", "cancelled")
TRIGGERS = ("schedule", "manual", "retry", "catch_up")
KEEP_RUNS = 200
KEEP_DAYS = 90

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

DEFAULT_ALERT = {"on_failure": True, "on_recovery": True, "include_output": True}

# Definition fields kept in the JSON blob (everything except id/bookkeeping).
_DATA_FIELDS = ("name", "command", "workdir", "env", "env_file", "schedule",
                "timeout_seconds", "retries", "retry_delay_seconds",
                "catch_up_window_minutes", "alert", "settings_schema", "settings_file")
_BOOKKEEPING = ("id", "revision", "created_at", "updated_at", "paused", "pause_changed_at")
_INT_BOUNDS = {
    "timeout_seconds": (1, 86400),
    "retries": (0, 10),
    "retry_delay_seconds": (0, 86400),
    "catch_up_window_minutes": (0, 1440),
}


class StoreError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _invalid(message: str) -> StoreError:
    return StoreError("invalid", message)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise _invalid("naive datetime")
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _validate_definition(job: Dict[str, Any]) -> None:
    if not isinstance(job.get("id"), str) or not ID_RE.fullmatch(job["id"]):
        raise _invalid("id must match ^[a-z0-9][a-z0-9-]{0,47}$")
    name = job.get("name")
    if not isinstance(name, str) or not name or len(name) > 100 or _CONTROL_RE.search(name):
        raise _invalid("name must be 1-100 characters without control characters")
    cmd = job.get("command")
    if (not isinstance(cmd, list) or not cmd
            or not all(isinstance(a, str) and "\0" not in a for a in cmd) or not cmd[0]):
        raise _invalid("command must be a non-empty list of strings without NUL, first non-empty")
    wd = job.get("workdir")
    if not isinstance(wd, str) or not wd.startswith("/") or "\0" in wd:
        raise _invalid("workdir must be an absolute path")
    env = job.get("env")
    if (not isinstance(env, dict)
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items())):
        raise _invalid("env must be a map of strings")
    ef = job.get("env_file")
    if ef is not None and not isinstance(ef, str):
        raise _invalid("env_file must be a string or null")
    sched = job.get("schedule")
    if (not isinstance(sched, dict) or set(sched) != {"cron", "tz"}
            or not isinstance(sched["cron"], list)
            or not all(isinstance(c, str) for c in sched["cron"])
            or not isinstance(sched["tz"], str)):
        raise _invalid('schedule must be {"cron": [str, ...], "tz": str}')
    try:
        for c in sched["cron"]:
            parse(c)
        validate_tz(sched["tz"])
    except CronError as exc:
        raise _invalid("schedule: %s" % exc)
    for field, (lo, hi) in _INT_BOUNDS.items():
        v = job.get(field)
        if not _is_int(v) or not lo <= v <= hi:
            raise _invalid("%s must be an integer in %d-%d" % (field, lo, hi))
    if not isinstance(job.get("paused"), bool):
        raise _invalid("paused must be a boolean")
    alert = job.get("alert")
    if (not isinstance(alert, dict) or set(alert) != set(DEFAULT_ALERT)
            or not all(isinstance(v, bool) for v in alert.values())):
        raise _invalid("alert must be {on_failure, on_recovery, include_output: bool}")
    for field in ("settings_file",):
        v = job.get(field)
        if v is not None and not isinstance(v, str):
            raise _invalid("%s must be a string or null" % field)


def _fill_defaults(job: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(job)
    out.setdefault("env", {})
    out.setdefault("env_file", None)
    out.setdefault("schedule", {"cron": [], "tz": "UTC"})
    out.setdefault("timeout_seconds", 3600)
    out.setdefault("retries", 0)
    out.setdefault("retry_delay_seconds", 60)
    out.setdefault("catch_up_window_minutes", 60)
    out.setdefault("paused", False)
    out.setdefault("alert", dict(DEFAULT_ALERT))
    out.setdefault("settings_schema", None)
    out.setdefault("settings_file", None)
    return out


_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    paused INTEGER NOT NULL DEFAULT 0,
    pause_changed_at TEXT,
    deleted INTEGER NOT NULL DEFAULT 0,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    trigger TEXT NOT NULL,
    status TEXT NOT NULL,
    scheduled_for TEXT,
    attempt INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    exit_code INTEGER,
    output_bytes INTEGER NOT NULL DEFAULT 0,
    truncated INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS runs_job ON runs (job_id, id);
CREATE TABLE IF NOT EXISTS alerts (
    key TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    since TEXT NOT NULL,
    last_sent_at TEXT,
    message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (name TEXT PRIMARY KEY, value TEXT);
"""

_RUN_COLS = ("id, job_id, trigger, status, scheduled_for, attempt, started_at, finished_at, "
             "exit_code, output_bytes, truncated")


def _run_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    d["truncated"] = bool(d["truncated"])
    return d


class Store:
    def __init__(self, path: str, clock: Clock) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _now(self) -> str:
        return _iso(self._clock.now())

    # ---- jobs -------------------------------------------------------------

    @staticmethod
    def _job_dict(row: sqlite3.Row) -> Dict[str, Any]:
        job = json.loads(row["data"])
        job.update(id=row["id"], revision=row["revision"], created_at=row["created_at"],
                   updated_at=row["updated_at"], paused=bool(row["paused"]),
                   pause_changed_at=row["pause_changed_at"])
        return job

    def _fetch_job(self, job_id: str, include_deleted: bool = False) -> Optional[sqlite3.Row]:
        row = self._db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None or (row["deleted"] and not include_deleted):
            return None
        return row

    @staticmethod
    def _blob(job: Dict[str, Any]) -> str:
        return json.dumps({k: job[k] for k in _DATA_FIELDS})

    def create_job(self, job: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(job, dict):
            raise _invalid("job must be an object")
        extra = set(job) - set(_DATA_FIELDS) - {"id", "paused"}
        if extra:
            raise _invalid("unknown fields: %s" % ", ".join(sorted(extra)))
        full = _fill_defaults(job)
        _validate_definition(full)
        now = self._now()
        with self._lock:
            if self._fetch_job(full["id"], include_deleted=True) is not None:
                raise StoreError("conflict", "job id already exists: %s" % full["id"])
            self._db.execute(
                "INSERT INTO jobs (id, revision, created_at, updated_at, paused, data) "
                "VALUES (?, 1, ?, ?, ?, ?)",
                (full["id"], now, now, int(full["paused"]), self._blob(full)))
            return self._job_dict(self._fetch_job(full["id"]))

    def update_job(self, job_id: str, changes: Dict[str, Any], revision: int) -> Dict[str, Any]:
        if not isinstance(changes, dict):
            raise _invalid("changes must be an object")
        bad = set(changes) - set(_DATA_FIELDS) - {"paused"}
        if bad:
            raise _invalid("cannot change or unknown fields: %s" % ", ".join(sorted(bad)))
        with self._lock:
            row = self._fetch_job(job_id)
            if row is None:
                raise StoreError("not_found", "no such job: %s" % job_id)
            if row["revision"] != revision:
                raise StoreError("conflict", "job revision is %d, not %s" % (row["revision"], revision))
            merged = self._job_dict(row)
            merged.update(changes)
            _validate_definition(merged)
            now = self._now()
            pause_changed = bool(row["paused"]) != merged["paused"]
            self._db.execute(
                "UPDATE jobs SET revision = revision + 1, updated_at = ?, paused = ?, "
                "pause_changed_at = ?, data = ? WHERE id = ?",
                (now, int(merged["paused"]), now if pause_changed else row["pause_changed_at"],
                 self._blob(merged), job_id))
            return self._job_dict(self._fetch_job(job_id))

    def delete_job(self, job_id: str) -> None:
        with self._lock:
            if self._fetch_job(job_id) is None:
                raise StoreError("not_found", "no such job: %s" % job_id)
            self._db.execute("UPDATE jobs SET deleted = 1, updated_at = ? WHERE id = ?",
                             (self._now(), job_id))

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._fetch_job(job_id)
            return None if row is None else self._job_dict(row)

    def list_jobs(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM jobs WHERE deleted = 0 ORDER BY id").fetchall()
            return [self._job_dict(r) for r in rows]

    def set_paused(self, job_id: str, paused: bool) -> None:
        if not isinstance(paused, bool):
            raise _invalid("paused must be a boolean")
        with self._lock:
            row = self._fetch_job(job_id)
            if row is None:
                raise StoreError("not_found", "no such job: %s" % job_id)
            if bool(row["paused"]) == paused:
                return
            now = self._now()
            self._db.execute("UPDATE jobs SET paused = ?, pause_changed_at = ?, updated_at = ? "
                             "WHERE id = ?", (int(paused), now, now, job_id))

    # ---- runs -------------------------------------------------------------

    def insert_run(self, job_id: str, trigger: str, scheduled_for: Optional[datetime],
                   attempt: int) -> int:
        if trigger not in TRIGGERS:
            raise _invalid("unknown trigger: %r" % (trigger,))
        with self._lock:
            if self._fetch_job(job_id) is None:
                raise StoreError("not_found", "no such job: %s" % job_id)
            cur = self._db.execute(
                "INSERT INTO runs (job_id, trigger, status, scheduled_for, attempt, started_at) "
                "VALUES (?, ?, 'running', ?, ?, ?)",
                (job_id, trigger, None if scheduled_for is None else _iso(scheduled_for),
                 attempt, self._now()))
            return int(cur.lastrowid)

    def finish_run(self, run_id: int, status: str, exit_code: Optional[int],
                   output_bytes: int, truncated: bool) -> None:
        if status not in RUN_STATUSES or status == "running":
            raise _invalid("unknown final status: %r" % (status,))
        with self._lock:
            cur = self._db.execute(
                "UPDATE runs SET status = ?, exit_code = ?, output_bytes = ?, truncated = ?, "
                "finished_at = ? WHERE id = ?",
                (status, exit_code, output_bytes, int(truncated), self._now(), run_id))
            if cur.rowcount == 0:
                raise StoreError("not_found", "no such run: %s" % run_id)

    def record_missed(self, job_id: str, scheduled_for: datetime) -> int:
        now = self._now()
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO runs (job_id, trigger, status, scheduled_for, attempt, "
                "started_at, finished_at) VALUES (?, 'schedule', 'missed', ?, 1, ?, ?)",
                (job_id, _iso(scheduled_for), now, now))
            return int(cur.lastrowid)

    def list_runs(self, job_id: str, status: Optional[str] = None, limit: int = 50,
                  before_id: Optional[int] = None) -> List[Dict[str, Any]]:
        sql = "SELECT %s FROM runs WHERE job_id = ?" % _RUN_COLS
        args: List[Any] = [job_id]
        if status is not None:
            sql += " AND status = ?"
            args.append(status)
        if before_id is not None:
            sql += " AND id < ?"
            args.append(before_id)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            return [_run_dict(r) for r in self._db.execute(sql, args).fetchall()]

    def get_run(self, run_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._db.execute("SELECT %s FROM runs WHERE id = ?" % _RUN_COLS,
                                   (run_id,)).fetchone()
            return None if row is None else _run_dict(row)

    def running_runs(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT %s FROM runs WHERE status = 'running' ORDER BY id"
                                    % _RUN_COLS).fetchall()
            return [_run_dict(r) for r in rows]

    def last_scheduled_for(self, job_id: str) -> Optional[datetime]:
        with self._lock:
            row = self._db.execute("SELECT MAX(scheduled_for) AS m FROM runs WHERE job_id = ?",
                                   (job_id,)).fetchone()
        return None if row["m"] is None else _parse_iso(row["m"])

    def prune(self, job_id: str) -> List[int]:
        """Drop runs beyond the newest 200 that are also older than 90 days.

        Returns the removed run ids so the caller can delete their output files.
        Runs still in progress are never removed.
        """
        cutoff = _iso(self._clock.now() - timedelta(days=KEEP_DAYS))
        with self._lock:
            rows = self._db.execute(
                "SELECT id FROM runs WHERE job_id = ? AND status != 'running' "
                "AND started_at < ? AND id NOT IN "
                "(SELECT id FROM runs WHERE job_id = ? ORDER BY id DESC LIMIT ?) ORDER BY id",
                (job_id, cutoff, job_id, KEEP_RUNS)).fetchall()
            ids = [r["id"] for r in rows]
            for rid in ids:
                self._db.execute("DELETE FROM runs WHERE id = ?", (rid,))
            return ids

    # ---- alerts / meta ----------------------------------------------------

    def get_alert(self, key: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._db.execute("SELECT * FROM alerts WHERE key = ?", (key,)).fetchone()
            return None if row is None else dict(row)

    def put_alert(self, key: str, state: str, since: str, last_sent_at: Optional[str],
                  message: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO alerts (key, state, since, last_sent_at, message) VALUES (?,?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET state=excluded.state, since=excluded.since, "
                "last_sent_at=excluded.last_sent_at, message=excluded.message",
                (key, state, since, last_sent_at, message))

    def firing_alerts(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM alerts WHERE state = 'firing' ORDER BY key")
            return [dict(r) for r in rows.fetchall()]

    def get_meta(self, name: str) -> Optional[str]:
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE name = ?", (name,)).fetchone()
            return None if row is None else row["value"]

    def set_meta(self, name: str, value: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO meta (name, value) VALUES (?, ?) "
                "ON CONFLICT(name) DO UPDATE SET value = excluded.value", (name, value))
