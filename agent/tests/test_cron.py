from datetime import datetime, timezone

import pytest

from panellab_agent.clock import Clock, FakeClock
from panellab_agent.cron import CronError, next_times, parse, validate_tz


def utc(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)


def test_daily_midnight_shanghai():
    s = parse("0 0 * * *")
    got = next_times(s, utc(2026, 11, 10, 15, 59), "Asia/Shanghai", 2)
    assert got == [utc(2026, 11, 10, 16, 0), utc(2026, 11, 11, 16, 0)]


def test_strictly_after():
    s = parse("0 0 * * *")
    got = next_times(s, utc(2026, 11, 10, 16, 0), "Asia/Shanghai", 1)
    assert got == [utc(2026, 11, 11, 16, 0)]


def test_lists_ranges_steps():
    got = next_times(parse("20,40,50 23 * * *"), utc(2026, 11, 10, 15, 0), "Asia/Shanghai", 3)
    assert [t.minute for t in got] == [20, 40, 50]
    got = next_times(parse("*/20 1-2 * * *"), utc(2026, 11, 10, 0, 0), "UTC", 6)
    assert [(t.hour, t.minute) for t in got] == [(1, 0), (1, 20), (1, 40), (2, 0), (2, 20), (2, 40)]
    got = next_times(parse("10-30/10 0 * * *"), utc(2026, 11, 10, 0, 0), "UTC", 3)
    assert [t.minute for t in got] == [10, 20, 30]


def test_dom_dow_or_rule():
    # 2026-11-13 is a Friday and the 13th; 2026-11-06 is a Friday, 2026-12-13 a Sunday
    got = next_times(parse("0 9 13 * 5"), utc(2026, 11, 1), "UTC", 4)
    assert [(t.month, t.day) for t in got] == [(11, 6), (11, 13), (11, 20), (11, 27)]
    got = next_times(parse("0 9 13 * 5"), utc(2026, 12, 1), "UTC", 3)
    assert [(t.month, t.day) for t in got] == [(12, 4), (12, 11), (12, 13)]


def test_dom_only_and_dow_only():
    got = next_times(parse("0 9 13 * *"), utc(2026, 11, 1), "UTC", 2)
    assert [(t.month, t.day) for t in got] == [(11, 13), (12, 13)]
    got = next_times(parse("0 9 * * 1"), utc(2026, 11, 1), "UTC", 2)
    assert [(t.month, t.day) for t in got] == [(11, 2), (11, 9)]


def test_sunday_is_0_and_7():
    a = next_times(parse("0 0 * * 7"), utc(2026, 11, 10), "UTC", 3)
    b = next_times(parse("0 0 * * 0"), utc(2026, 11, 10), "UTC", 3)
    assert a == b
    assert all(t.weekday() == 6 for t in a)


def test_spring_forward_gap_skipped():
    got = next_times(parse("30 2 * * *"), utc(2027, 3, 13, 12, 0), "America/New_York", 1)
    # 2027-03-14 02:30 does not exist in New York; next run is 03-15 02:30 EDT = 06:30 UTC
    assert got == [utc(2027, 3, 15, 6, 30)]


def test_fall_back_runs_once():
    got = next_times(parse("30 1 * * *"), utc(2026, 11, 1, 0, 0), "America/New_York", 2)
    # first 01:30 is EDT (05:30 UTC); the repeated 01:30 EST is not used
    assert got == [utc(2026, 11, 1, 5, 30), utc(2026, 11, 2, 6, 30)]


def test_leap_day():
    got = next_times(parse("0 0 29 2 *"), utc(2026, 11, 10), "UTC", 1)
    assert got == [utc(2028, 2, 29, 0, 0)]


def test_count_zero():
    assert next_times(parse("* * * * *"), utc(2026, 1, 1), "UTC", 0) == []


@pytest.mark.parametrize(
    "bad",
    ["", "* * * *", "* * * * * *", "60 * * * *", "* 24 * * *", "* * 0 * *", "* * * 13 *",
     "* * * * 8", "*/0 * * * *", "MON * * * *", "* * * JAN *", "1-", "5-1 * * * *",
     "1,,2 * * * *", "-1 * * * *", "1/5 * * * *", "*/ * * * *", "a * * * *"],
)
def test_rejects(bad):
    with pytest.raises(CronError):
        parse(bad)


def test_bad_timezone():
    with pytest.raises(CronError):
        validate_tz("Mars/Base")
    with pytest.raises(CronError):
        next_times(parse("* * * * *"), utc(2026, 1, 1), "Mars/Base", 1)
    validate_tz("Asia/Shanghai")


def test_fake_clock():
    c = FakeClock(utc(2026, 1, 1))
    assert c.now() == utc(2026, 1, 1)
    m = c.monotonic()
    c.advance(90)
    assert c.now() == utc(2026, 1, 1, 0, 1) .replace(second=30)
    assert c.monotonic() == m + 90
    assert Clock().now().tzinfo is not None
