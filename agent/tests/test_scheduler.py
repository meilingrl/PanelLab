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


def test_overlap_records_only_latest_eligible():
    j = job("0 * * * *", window=180)
    d = plan_tick([j], {"j1": dt(0, 30)}, dt(4, 10), {"j1"})
    assert d.start == []
    assert d.skipped_overlap == [("j1", dt(4))]
    assert d.missed == [("j1", dt(1))]


def test_bad_job_does_not_abort_tick():
    bad = job("not a cron", jid="bad")
    badtz = job("0 3 * * *", jid="badtz")
    badtz["schedule"]["tz"] = "Nope/Nowhere"
    nokey = {"id": "nokey"}
    good = job("0 3 * * *", jid="good")
    ls = {k: dt(2) for k in ("bad", "badtz", "nokey", "good")}
    d = plan_tick([bad, badtz, nokey, good], ls, dt(3, 0), set())
    assert d.start == [Start("good", "schedule", dt(3), 0)]
    assert sorted(e[0] for e in d.errors) == ["bad", "badtz", "nokey"]
    assert all(isinstance(e[1], str) and e[1] for e in d.errors)


def test_boundary_on_time_two_minutes():
    j = job("0 3 * * *", window=60)
    d = plan_tick([j], {"j1": dt(2)}, dt(3, 2), set())
    assert d.start == [Start("j1", "schedule", dt(3), 0)]
    d = plan_tick([j], {"j1": dt(2)}, dt(3, 2) + timedelta(seconds=1), set())
    assert d.start == [Start("j1", "catch_up", dt(3), 2)]


def test_boundary_window_edge():
    j = job("0 3 * * *", window=30)
    d = plan_tick([j], {"j1": dt(2)}, dt(3, 30), set())
    assert d.start == [Start("j1", "catch_up", dt(3), 30)] and d.missed == []
    d = plan_tick([j], {"j1": dt(2)}, dt(3, 30) + timedelta(seconds=1), set())
    assert d.start == [] and d.missed == [("j1", dt(3))]


def test_due_equal_now_is_on_time():
    d = plan_tick([job("0 3 * * *")], {"j1": dt(2)}, dt(3), set())
    assert d.start == [Start("j1", "schedule", dt(3), 0)]


def test_due_equal_last_seen_excluded():
    d = plan_tick([job("0 3 * * *")], {"j1": dt(3)}, dt(3, 1), set())
    assert d == Decisions([], [], [])


def test_window_zero_two_vs_three_minutes_late():
    j = job("0 3 * * *", window=0)
    d = plan_tick([j], {"j1": dt(2)}, dt(3, 2), set())
    assert d.start == [Start("j1", "schedule", dt(3), 0)]
    d = plan_tick([j], {"j1": dt(2)}, dt(3, 3), set())
    assert d.start == [] and d.missed == [("j1", dt(3))]


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
