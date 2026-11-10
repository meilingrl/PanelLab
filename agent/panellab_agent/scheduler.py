"""Pure scheduling decisions: which runs to start, which were missed or skipped.

No database access and no clock reads; the daemon passes everything in.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, NamedTuple, Set, Tuple

from . import cron

ON_TIME = timedelta(minutes=2)
MAX_DUE = 1000


class Start(NamedTuple):
    job_id: str
    trigger: str  # "schedule" | "catch_up"
    scheduled_for: datetime
    late_minutes: int


class Decisions(NamedTuple):
    start: List[Start]
    missed: List[Tuple[str, datetime]]
    skipped_overlap: List[Tuple[str, datetime]]


def _parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _due_times(job: dict, start: datetime, now: datetime) -> List[datetime]:
    """Due times in (start, now], merged across expressions, ascending, last MAX_DUE kept."""
    sched = job["schedule"]
    found = set()
    for expr in sched.get("cron", []):
        spec = cron.parse(expr)
        cursor = start
        while True:
            batch = cron.next_times(spec, cursor, sched["tz"], MAX_DUE)
            if not batch:
                break
            inside = [t for t in batch if t <= now]
            found.update(inside)
            if len(inside) < len(batch) or len(batch) < MAX_DUE:
                break
            cursor = batch[-1]
    return sorted(found)[-MAX_DUE:]


def plan_tick(
    jobs: List[dict],
    last_seen: Dict[str, datetime],
    now: datetime,
    running: Set[str],
) -> Decisions:
    starts: List[Start] = []
    missed: List[Tuple[str, datetime]] = []
    skipped: List[Tuple[str, datetime]] = []
    for job in jobs:
        if job.get("paused"):
            continue
        jid = job["id"]
        created = _parse_ts(job["created_at"])
        begin = last_seen.get(jid) or created
        begin = max(begin, created)
        if job.get("pause_changed_at"):
            begin = max(begin, _parse_ts(job["pause_changed_at"]))
        dues = _due_times(job, begin, now)
        if not dues:
            continue
        window = timedelta(minutes=job.get("catch_up_window_minutes", 0))
        eligible = []
        for due in dues:
            age = now - due
            if age <= ON_TIME or age <= window:
                eligible.append(due)
            else:
                missed.append((jid, due))
        if not eligible:
            continue
        if jid in running:
            skipped.extend((jid, due) for due in eligible)
            continue
        latest = eligible[-1]
        age = now - latest
        if age <= ON_TIME:
            starts.append(Start(jid, "schedule", latest, 0))
        else:
            starts.append(Start(jid, "catch_up", latest, int(age.total_seconds() // 60)))
    return Decisions(starts, missed, skipped)
