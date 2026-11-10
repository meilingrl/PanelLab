from datetime import datetime, timedelta, timezone

from panellab_agent.scheduler import Decisions, Start, plan_tick

UTC = timezone.utc


def dt(h, m=0, day=1):
    return datetime(2026, 1, day, h, m, tzinfo=UTC)


def job(cron="0 0 * * *", window=60, paused=False, created=None, pause_changed=None, jid="j1"):
    return {
        "id": jid,
        "schedule": {"cron": [cron] if isinstance(cron, str) else list(cron), "tz": "UTC"},
        "catch_up_window_minutes": window,
        "paused": paused,
        "created_at": (created or datetime(2025, 1, 1, tzinfo=UTC)).isoformat(),
        "updated_at": (created or datetime(2025, 1, 1, tzinfo=UTC)).isoformat(),
        "pause_changed_at": pause_changed.isoformat() if pause_changed else None,
    }


def test_on_time_start():
    d = plan_tick([job("0 3 * * *")], {"j1": dt(2, 59, 1)}, dt(3, 0, 1) + timedelta(seconds=1), set())
    assert d.start == [Start("j1", "schedule", dt(3), 0)]
    assert d.missed == [] and d.skipped_overlap == []


def test_nothing_due():
    d = plan_tick([job("0 3 * * *")], {"j1": dt(4)}, dt(5), set())
    assert d == Decisions([], [], [])


def test_downtime_within_window_merges_into_one_catch_up():
    j = job("20,40,50 23 * * *", window=60)
    d = plan_tick([j], {"j1": dt(23, 0)}, dt(0, 10, 2), set())
    assert d.start == [Start("j1", "catch_up", dt(23, 50), 20)]
    assert d.missed == []


def test_beyond_window_records_each_missed():
    d = plan_tick([job("0 0 * * *", window=60)], {"j1": dt(23, 0, 1)}, dt(9, 0, 2), set())
    assert d.start == []
    assert d.missed == [("j1", dt(0, 0, 2))]


def test_partly_beyond_window():
    j = job("0 * * * *", window=60)
    d = plan_tick([j], {"j1": dt(0, 30)}, dt(4, 10), set())
    assert d.start == [Start("j1", "catch_up", dt(4), 10)]
    assert d.missed == [("j1", dt(1)), ("j1", dt(2)), ("j1", dt(3))]


def test_window_zero_never_catches_up():
    d = plan_tick([job("0 3 * * *", window=0)], {"j1": dt(2)}, dt(3, 30), set())
    assert d.start == [] and d.missed == [("j1", dt(3))]


def test_window_zero_still_starts_on_time():
    d = plan_tick([job("0 3 * * *", window=0)], {"j1": dt(2)}, dt(3, 1), set())
    assert d.start == [Start("j1", "schedule", dt(3), 0)]


def test_overlap_skipped():
    d = plan_tick([job("0 3 * * *")], {"j1": dt(2)}, dt(3, 0), {"j1"})
    assert d.start == [] and d.skipped_overlap == [("j1", dt(3))]


def test_overlap_beyond_window_still_missed():
    d = plan_tick([job("0 3 * * *", window=10)], {"j1": dt(2)}, dt(9), {"j1"})
    assert d.missed == [("j1", dt(3))] and d.skipped_overlap == [] and d.start == []


def test_paused_job_neither_runs_nor_misses():
    d = plan_tick([job("0 3 * * *", paused=True)], {"j1": dt(2)}, dt(9), set())
    assert d == Decisions([], [], [])


def test_resume_counts_from_pause_changed_at():
    j = job("0 3 * * *", pause_changed=dt(8))
    d = plan_tick([j], {"j1": dt(1)}, dt(9), set())
    assert d == Decisions([], [], [])


def test_new_job_does_not_backfill_history():
    j = job("0 * * * *", created=dt(5, 30))
    d = plan_tick([j], {}, dt(6, 0, 1) + timedelta(seconds=5), set())
    assert d.start == [Start("j1", "schedule", dt(6), 0)]
    assert d.missed == []
    # last_seen older than created_at is clamped
    d2 = plan_tick([j], {"j1": dt(0)}, dt(6, 5), set())
    assert d2.start == [Start("j1", "catch_up", dt(6), 5)] and d2.missed == []


def test_latest_wins_when_on_time_and_late_both_exist():
    j = job("50,59 * * * *", window=120)
    d = plan_tick([j], {"j1": dt(0, 30)}, dt(1, 0), set())
    assert d.start == [Start("j1", "schedule", dt(0, 59), 0)]
    j2 = job("0,50 * * * *", window=120)
    d = plan_tick([j2], {"j1": dt(0, 30)}, dt(1, 1), set())
    assert d.start == [Start("j1", "schedule", dt(1, 0), 0)]


def test_multiple_exprs_merged_and_deduped():
    j = job(["0 3 * * *", "0 3 * * *", "30 2 * * *"], window=120)
    d = plan_tick([j], {"j1": dt(2)}, dt(3, 20), set())
    assert d.start == [Start("j1", "catch_up", dt(3), 20)]
    assert d.missed == []


def test_empty_cron_list():
    d = plan_tick([job([])], {"j1": dt(0)}, dt(9), set())
    assert d == Decisions([], [], [])


def test_scan_capped_at_1000():
    j = job("* * * * *", window=0)
    d = plan_tick([j], {"j1": dt(0, 0, 1)}, dt(0, 0, 5), set())
    # last 1000 due times considered: 997 older ones missed, newest 3 are on time
    assert len(d.missed) == 997
    assert d.start == [Start("j1", "schedule", dt(0, 0, 5), 0)]
