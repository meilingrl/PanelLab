import os
import stat
from datetime import datetime, timedelta, timezone

import pytest

from panellab_agent.clock import FakeClock
from panellab_agent.secret_store import Secrets
from panellab_agent.store import Store, StoreError

T0 = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


def make_job(**over):
    job = {"id": "backup", "name": "Backup", "command": ["/bin/echo", "hi"],
           "workdir": "/tmp"}
    job.update(over)
    return job


@pytest.fixture
def clock():
    return FakeClock(T0)


@pytest.fixture
def store(tmp_path, clock):
    return Store(str(tmp_path / "agent.db"), clock)


def test_create_and_get_roundtrip(store):
    created = store.create_job(make_job(schedule={"cron": ["0 0 * * *"], "tz": "Asia/Shanghai"}))
    got = store.get_job("backup")
    assert got == created
    assert got["revision"] == 1
    assert got["env"] == {} and got["env_file"] is None
    assert got["timeout_seconds"] == 3600 and got["retries"] == 0
    assert got["retry_delay_seconds"] == 60 and got["catch_up_window_minutes"] == 60
    assert got["paused"] is False and got["pause_changed_at"] is None
    assert got["alert"] == {"on_failure": True, "on_recovery": True, "include_output": True}
    assert got["schedule"]["tz"] == "Asia/Shanghai"
    assert got["created_at"] == "2026-11-10T12:00:00+00:00"
    assert got["updated_at"] == got["created_at"]
    assert got["settings_schema"] is None and got["settings_file"] is None
    assert store.get_job("nope") is None
    assert store.list_jobs() == [got]


def test_default_schedule(store):
    assert store.create_job(make_job())["schedule"] == {"cron": [], "tz": "UTC"}


def test_update_requires_current_revision(store, clock):
    store.create_job(make_job())
    clock.advance(60)
    upd = store.update_job("backup", {"name": "New"}, 1)
    assert upd["revision"] == 2 and upd["name"] == "New"
    assert upd["updated_at"] == "2026-11-10T12:01:00+00:00"
    with pytest.raises(StoreError) as e:
        store.update_job("backup", {"name": "Again"}, 1)
    assert e.value.code == "conflict"
    with pytest.raises(StoreError) as e:
        store.update_job("missing", {"name": "x"}, 1)
    assert e.value.code == "not_found"


@pytest.mark.parametrize("changes", [{"id": "x"}, {"revision": 5}, {"created_at": "x"}, {"bogus": 1},
                                     {"timeout_seconds": 0}])
def test_update_rejects_invalid(store, changes):
    store.create_job(make_job())
    with pytest.raises(StoreError) as e:
        store.update_job("backup", changes, 1)
    assert e.value.code == "invalid"


@pytest.mark.parametrize("over", [
    {"id": "Bad"}, {"id": "ok\n"}, {"id": "-a"}, {"id": ""}, {"id": "a" * 49},
    {"name": ""}, {"name": "x" * 101}, {"name": "a\nb"},
    {"command": []}, {"command": "echo"}, {"command": [""]}, {"command": ["", "x"]}, {"command": ["a\0b"]}, {"command": [1]},
    {"workdir": "relative"}, {"workdir": ""},
    {"schedule": {"cron": "0 0 * * *", "tz": "UTC"}},
    {"schedule": {"cron": ["bad cron"], "tz": "UTC"}},
    {"schedule": {"cron": [], "tz": "Not/AZone"}},
    {"schedule": ["x"]},
    {"timeout_seconds": 0}, {"timeout_seconds": 86401}, {"timeout_seconds": True},
    {"retries": -1}, {"retries": 11},
    {"retry_delay_seconds": -1}, {"retry_delay_seconds": 86401},
    {"catch_up_window_minutes": -1}, {"catch_up_window_minutes": 1441},
    {"alert": {"on_failure": "yes", "on_recovery": True, "include_output": True}},
    {"alert": {"on_failure": True}},
    {"paused": "no"}, {"env": {"A": 1}}, {"env": []},
    {"unknown": 1},
])
def test_rejects_invalid_job(store, over):
    with pytest.raises(StoreError) as e:
        store.create_job(make_job(**over))
    assert e.value.code == "invalid"


def test_command_allows_empty_later_args(store):
    assert store.create_job(make_job(command=["prog", ""]))["command"] == ["prog", ""]


def test_duplicate_id_conflict(store):
    store.create_job(make_job())
    with pytest.raises(StoreError) as e:
        store.create_job(make_job())
    assert e.value.code == "conflict"


def test_delete_keeps_history_but_hides_job(store):
    store.create_job(make_job())
    rid = store.insert_run("backup", "schedule", T0, 1)
    store.delete_job("backup")
    assert store.list_jobs() == []
    assert store.get_job("backup") is None
    assert store.get_run(rid)["job_id"] == "backup"
    with pytest.raises(StoreError) as e:
        store.delete_job("backup")
    assert e.value.code == "not_found"
    with pytest.raises(StoreError) as e:
        store.create_job(make_job())
    assert e.value.code == "conflict"
    with pytest.raises(StoreError) as e:
        store.delete_job("never")
    assert e.value.code == "not_found"


def test_set_paused_tracks_change(store, clock):
    store.create_job(make_job())
    clock.advance(10)
    store.set_paused("backup", True)
    j = store.get_job("backup")
    assert j["paused"] is True and j["pause_changed_at"] == "2026-11-10T12:00:10+00:00"
    clock.advance(10)
    store.set_paused("backup", True)  # no change
    assert store.get_job("backup")["pause_changed_at"] == "2026-11-10T12:00:10+00:00"
    store.set_paused("backup", False)
    assert store.get_job("backup")["pause_changed_at"] == "2026-11-10T12:00:20+00:00"
    with pytest.raises(StoreError) as e:
        store.set_paused("zzz", True)
    assert e.value.code == "not_found"


def test_run_lifecycle(store, clock):
    store.create_job(make_job())
    sched = T0 - timedelta(minutes=1)
    rid = store.insert_run("backup", "schedule", sched, 1)
    run = store.get_run(rid)
    assert run["status"] == "running" and run["trigger"] == "schedule" and run["attempt"] == 1
    assert run["started_at"] == "2026-11-10T12:00:00+00:00"
    assert run["scheduled_for"] == "2026-11-10T11:59:00+00:00"
    assert [r["id"] for r in store.running_runs()] == [rid]
    clock.advance(5)
    store.finish_run(rid, "failed", 2, 123, True)
    run = store.get_run(rid)
    assert run["status"] == "failed" and run["exit_code"] == 2
    assert run["output_bytes"] == 123 and run["truncated"] is True
    assert run["finished_at"] == "2026-11-10T12:00:05+00:00"
    assert store.running_runs() == []
    assert store.get_run(9999) is None
    with pytest.raises(StoreError):
        store.insert_run("backup", "weird", sched, 1)
    with pytest.raises(StoreError):
        store.finish_run(rid, "weird", 0, 0, False)
    with pytest.raises(StoreError):
        store.insert_run("ghost", "schedule", sched, 1)


def test_missed_and_last_scheduled_for(store):
    store.create_job(make_job())
    assert store.last_scheduled_for("backup") is None
    t1, t2 = T0 - timedelta(hours=2), T0 - timedelta(hours=1)
    store.record_missed("backup", t1)
    r = store.insert_run("backup", "schedule", t2, 1)
    store.insert_run("backup", "manual", None, 1)
    assert store.last_scheduled_for("backup") == t2
    runs = store.list_runs("backup")
    assert len(runs) == 3
    assert any(x["status"] == "missed" and x["scheduled_for"] == t1.isoformat() for x in runs)
    assert store.get_run(r)["status"] == "running"


def test_list_runs_filter_and_paging(store):
    store.create_job(make_job())
    ids = [store.insert_run("backup", "schedule", T0, 1) for _ in range(5)]
    for i in ids[:2]:
        store.finish_run(i, "succeeded", 0, 0, False)
    runs = store.list_runs("backup")
    assert [r["id"] for r in runs] == ids[::-1]  # newest first
    assert [r["id"] for r in store.list_runs("backup", status="succeeded")] == ids[:2][::-1]
    assert [r["id"] for r in store.list_runs("backup", limit=2, before_id=ids[3])] == [ids[2], ids[1]]


def _bulk_runs(store, clock, n, age_days):
    # insert n finished runs, all started age_days ago
    base = clock.now()
    clock._now = base - timedelta(days=age_days)
    ids = []
    for _ in range(n):
        rid = store.insert_run("backup", "schedule", None, 1)
        store.finish_run(rid, "succeeded", 0, 0, False)
        ids.append(rid)
    clock._now = base
    return ids


def test_prune_keeps_200_or_90_days(store, clock):
    store.create_job(make_job())
    ids = _bulk_runs(store, clock, 250, 30)
    assert store.prune("backup") == []
    assert len(store.list_runs("backup", limit=1000)) == 250

    store.create_job(make_job(id="old"))
    base = clock.now()
    clock._now = base - timedelta(days=100)
    olds = []
    for _ in range(250):
        rid = store.insert_run("old", "schedule", None, 1)
        store.finish_run(rid, "succeeded", 0, 0, False)
        olds.append(rid)
    clock._now = base
    removed = store.prune("old")
    assert sorted(removed) == sorted(olds[:50])
    assert len(store.list_runs("old", limit=1000)) == 200


def test_prune_never_removes_running(store, clock):
    store.create_job(make_job())
    base = clock.now()
    clock._now = base - timedelta(days=200)
    rid = store.insert_run("backup", "schedule", None, 1)  # still running, ancient
    clock._now = base
    assert store.prune("backup") == []
    assert store.get_run(rid) is not None


def test_alerts_and_meta(store):
    assert store.get_alert("k") is None
    store.put_alert("k", "firing", "2026-11-10T12:00:00+00:00", None, "boom")
    a = store.get_alert("k")
    assert a == {"key": "k", "state": "firing", "since": "2026-11-10T12:00:00+00:00",
                 "last_sent_at": None, "message": "boom"}
    store.put_alert("j", "ok", "2026-11-10T12:00:00+00:00", "2026-11-10T12:01:00+00:00", "fine")
    assert [x["key"] for x in store.firing_alerts()] == ["k"]
    store.put_alert("k", "ok", "2026-11-10T13:00:00+00:00", None, "")
    assert store.firing_alerts() == []
    assert store.get_meta("x") is None
    store.set_meta("x", "1")
    store.set_meta("x", "2")
    assert store.get_meta("x") == "2"


def test_persists_across_reopen(tmp_path, clock):
    p = str(tmp_path / "a.db")
    s = Store(p, clock)
    s.create_job(make_job())
    s.close()
    assert Store(p, clock).get_job("backup")["name"] == "Backup"


def test_secrets_file_mode_0600_and_never_in_sqlite(tmp_path, clock):
    db = tmp_path / "agent.db"
    store = Store(str(db), clock)
    store.create_job(make_job(env={"PLAIN": "visible"}))
    sec = Secrets(str(tmp_path / "secrets.json"))
    sec.set_job_secrets("backup", {"TOKEN": "s3cr3t-token-value"})
    sec.set_smtp_password("smtp-pass-value")
    store.insert_run("backup", "manual", None, 1)
    store.close()
    mode = stat.S_IMODE(os.stat(tmp_path / "secrets.json").st_mode)
    assert mode == 0o600
    blob = b"".join(p.read_bytes() for p in tmp_path.iterdir() if p.name.startswith("agent.db"))
    assert b"s3cr3t-token-value" not in blob and b"smtp-pass-value" not in blob
    assert b"visible" in blob
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith("secrets")] == ["secrets.json"]


def test_secret_changes_keep_unchanged_and_delete_none(tmp_path):
    sec = Secrets(str(tmp_path / "secrets.json"))
    assert sec.get() == {}
    assert sec.job_secret_names("j") == [] and sec.job_env("j") == {}
    sec.set_job_secrets("j", {"A": "1", "B": "2"})
    sec.set_job_secrets("j", {"B": None, "C": "3"})
    assert sec.job_secret_names("j") == ["A", "C"]
    assert sec.job_env("j") == {"A": "1", "C": "3"}
    sec.set_job_secrets("j", {})
    assert sec.job_env("j") == {"A": "1", "C": "3"}
    sec.set_smtp_password("pw")
    assert sec.smtp_password() == "pw"
    sec.set_smtp_password(None)
    assert sec.smtp_password() is None
    assert Secrets(str(tmp_path / "secrets.json")).job_env("j") == {"A": "1", "C": "3"}
    with pytest.raises(StoreError):
        sec.set_job_secrets("j", {"bad name": "x"})
    with pytest.raises(StoreError):
        sec.set_job_secrets("j", {"TOKEN\n": "x"})


def test_record_skipped_mirrors_missed(store):
    store.create_job(make_job())
    t1 = T0 - timedelta(minutes=1)
    rid = store.record_skipped("backup", t1)
    run = store.get_run(rid)
    assert run["status"] == "skipped_overlap" and run["trigger"] == "schedule"
    assert run["scheduled_for"] == t1.isoformat() and run["finished_at"] is not None
