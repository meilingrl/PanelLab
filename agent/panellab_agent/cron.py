"""Five-field cron parsing and next-run computation.

Supports numbers, `*`, `,`, `-` and `/` only (no month/weekday names).
Day-of-month and day-of-week follow the Vixie rule: when both are restricted
a day matches if either matches.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import FrozenSet, List
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class CronError(ValueError):
    pass


@dataclass(frozen=True)
class CronSpec:
    minutes: FrozenSet[int]
    hours: FrozenSet[int]
    doms: FrozenSet[int]
    months: FrozenSet[int]
    dows: FrozenSet[int]  # 0-6, Sunday = 0
    dom_star: bool
    dow_star: bool


def _parse_field(text: str, lo: int, hi: int, name: str) -> FrozenSet[int]:
    values = set()
    for part in text.split(","):
        base, slash, step_s = part.partition("/")
        step = 1
        if slash:
            if not (step_s.isascii() and step_s.isdigit()) or int(step_s) < 1:
                raise CronError("%s字段的步长无效：%r" % (name, part))
            step = int(step_s)
        if base == "*":
            start, end = lo, hi
        else:
            a, dash, b = base.partition("-")
            if not (a.isascii() and a.isdigit()):
                raise CronError("%s字段的值无效：%r" % (name, part))
            start = int(a)
            if dash:
                if not (b.isascii() and b.isdigit()):
                    raise CronError("%s字段的范围无效：%r" % (name, part))
                end = int(b)
            elif slash:
                raise CronError("%s字段的步长需要配合 * 或范围：%r" % (name, part))
            else:
                end = start
        if start < lo or end > hi or start > end:
            raise CronError("%s字段的值超出范围：%r" % (name, part))
        values.update(range(start, end + 1, step))
    return frozenset(values)


def parse(expr: str) -> CronSpec:
    fields = expr.split()
    if len(fields) != 5:
        raise CronError("cron 表达式需要 5 个字段，实际 %d 个" % len(fields))
    minute, hour, dom, month, dow = fields
    dows = _parse_field(dow, 0, 7, "星期")
    return CronSpec(
        minutes=_parse_field(minute, 0, 59, "分钟"),
        hours=_parse_field(hour, 0, 23, "小时"),
        doms=_parse_field(dom, 1, 31, "日期"),
        months=_parse_field(month, 1, 12, "月份"),
        dows=frozenset(d % 7 for d in dows),
        dom_star=dom.startswith("*"),
        dow_star=dow.startswith("*"),
    )


def validate_tz(tz: str) -> None:
    _zone(tz)


def _zone(tz: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError, OSError, KeyError) as exc:
        raise CronError("未知的时区：%r" % (tz,)) from exc


def _day_matches(spec: CronSpec, day) -> bool:
    if day.month not in spec.months:
        return False
    dom_ok = day.day in spec.doms
    dow_ok = (day.weekday() + 1) % 7 in spec.dows
    if spec.dom_star or spec.dow_star:
        return dom_ok and dow_ok
    return dom_ok or dow_ok


def next_times(spec: CronSpec, after: datetime, tz: str, count: int) -> List[datetime]:
    """Next `count` run times (UTC) strictly after `after`."""
    zone = _zone(tz)
    results: List[datetime] = []
    if count <= 0:
        return results
    after_utc = after.astimezone(timezone.utc)
    day = after_utc.astimezone(zone).date() - timedelta(days=1)
    last_day = day + timedelta(days=366 * 9)  # covers 29 Feb across a century gap
    hours = sorted(spec.hours)
    minutes = sorted(spec.minutes)
    while day <= last_day and len(results) < count:
        if _day_matches(spec, day):
            for h in hours:
                for m in minutes:
                    local = datetime(day.year, day.month, day.day, h, m, tzinfo=zone)  # fold=0
                    cand = local.astimezone(timezone.utc)
                    if cand.astimezone(zone).replace(tzinfo=None) != local.replace(tzinfo=None):
                        continue  # nonexistent local time (DST gap)
                    if cand > after_utc:
                        results.append(cand)
                        if len(results) == count:
                            return results
        day += timedelta(days=1)
    return results
