import json
import os
from datetime import timedelta

import pytest

from conftest import T0, call, err, job_def, ok, wait_run
from panellab_agent import protocol


def test_protocol_mismatch(rig):
    a = rig.agent()
    resp = protocol.handle(a, {"protocol_version": 2, "op": "status", "args": {}})
    assert resp["ok"] is False and resp["error"]["code"] == "protocol_mismatch"
    assert resp["error"]["message"]


def test_unknown_op_bad_request(rig):
    a = rig.agent()
    assert err(protocol.handle(a, {"protocol_version": 1, "op": "jobs.explode", "args": {}})) \
        == "bad_request"
    assert err(protocol.handle(a, ["not", "a", "dict"])) == "bad_request"
    assert err(protocol.handle(a, {"protocol_version": 1, "op": "status", "args": []})) \
        == "bad_request"
    assert err(protocol.handle(a, {"op": "status"})) == "bad_request"
    assert err(call(a, "jobs.get")) == "bad_request"  # missing id


def test_create_run_now_and_read_output(rig, tmp_path):
    a = rig.agent()
    job = ok(call(a, "jobs.create", job=job_def(tmp_path)))
    assert job["id"] == "j" and job["revision"] == 1
    run_id = ok(call(a, "jobs.run_now", id="j"))["run_id"]
    run = wait_run(a, run_id)
    assert run["status"] == "succeeded" and run["trigger"] == "manual"
    out = ok(call(a, "runs.output", run_id=run_id, offset=0, limit=1000))
    assert out["data"] == "hello\n" and out["eof"] is True
    runs = ok(call(a, "runs.list", job_id="j"))["runs"]
    assert [r["id"] for r in runs] == [run_id]
    assert err(call(a, "runs.output", run_id=run_id, offset=-1)) == "invalid"
    assert err(call(a, "runs.output", run_id=99999, offset=0)) == "not_found"
    assert err(call(a, "jobs.run_now", id="nope")) == "not_found"


def test_create_defaults_tz_to_server_zone(rig, tmp_path):
    a = rig.agent()
    d = job_def(tmp_path)
    d["schedule"] = {"cron": ["0 9 * * *"]}
    assert ok(call(a, "jobs.create", job=d))["schedule"]["tz"] == "Asia/Shanghai"
    d2 = job_def(tmp_path, id="k")
    del d2["schedule"]
    assert ok(call(a, "jobs.create", job=d2))["schedule"] == {"cron": [], "tz": "Asia/Shanghai"}


def test_update_conflict_maps_to_conflict_code(rig, tmp_path):
    a = rig.agent()
    ok(call(a, "jobs.create", job=job_def(tmp_path)))
    j2 = ok(call(a, "jobs.update", id="j", revision=1, changes={"name": "J2"}))
    assert j2["revision"] == 2 and j2["name"] == "J2"
    assert err(call(a, "jobs.update", id="j", revision=1, changes={"name": "J3"})) == "conflict"
    assert err(call(a, "jobs.update", id="j", revision=2, changes={"timeout_seconds": 0})) \
        == "invalid"
    assert err(call(a, "jobs.update", id="x", revision=1, changes={})) == "not_found"
    assert err(call(a, "jobs.create", job=job_def(tmp_path))) == "conflict"


def test_reads_never_contain_secrets(rig, tmp_path):
    a = rig.agent()
    d = job_def(tmp_path)
    ok(call(a, "jobs.create", job=d, secret_env={"API_KEY": "sk-job-secret-123"}))
    ok(call(a, "alerts.configure",
            smtp={"host": "smtp.example.com", "port": 587, "security": "starttls",
                  "username": "u@example.com", "from": "u@example.com",
                  "to": ["me@example.com"], "password": "smtp-pass-XYZ"}))
    reads = [call(a, "status"), call(a, "jobs.get", id="j"), call(a, "jobs.list"),
             call(a, "alerts.get")]
    text = json.dumps(reads, ensure_ascii=False)
    assert "sk-job-secret-123" not in text and "smtp-pass-XYZ" not in text
    job = ok(reads[1])
    assert job["secret_env"] == [{"name": "API_KEY", "status": "已设置"}]
    alerts = ok(reads[3])
    assert alerts["smtp_password"] == "已设置"
    assert ok(reads[0])["alerts_configured"] is True
    with open(rig.config, encoding="utf-8") as f:
        assert "smtp-pass-XYZ" not in f.read()
    assert (os.stat(rig.config).st_mode & 0o777) == 0o640
    # the secrets really are stored and used
    assert a.secrets.smtp_password() == "smtp-pass-XYZ"
    assert a.secrets.job_env("j") == {"API_KEY": "sk-job-secret-123"}
    # three-way: absent keeps, None deletes
    ok(call(a, "alerts.configure", ssh_enabled=False))
    assert a.secrets.smtp_password() == "smtp-pass-XYZ"
    ok(call(a, "jobs.update", id="j", revision=1, changes={},
            secret_env={"API_KEY": None, "OTHER": "v"}))
    assert a.secrets.job_env("j") == {"OTHER": "v"}
    smtp = dict(ok(call(a, "alerts.get"))["smtp"], password=None)
    ok(call(a, "alerts.configure", smtp=smtp))
    assert a.secrets.smtp_password() is None
    assert ok(call(a, "alerts.get"))["smtp_password"] == "未设置"


@pytest.mark.parametrize("args", [
    {"smtp": {"host": 1, "port": 25, "security": "none", "from": "a@b", "to": ["x@y"]}},
    {"smtp": {"host": "h", "port": 0, "security": "none", "from": "a@b", "to": ["x@y"]}},
    {"smtp": {"host": "h", "port": 25, "security": "tls", "from": "a@b", "to": ["x@y"]}},
    {"smtp": {"host": "h", "port": 25, "security": "none", "from": "a@b", "to": "x@y"}},
    {"smtp": {"host": "h", "port": 25, "security": "none", "from": "a@b", "to": []}},
    {"smtp": {"host": "h", "port": 25, "security": "none", "from": "a@b",
              "to": ["x@y\r\nBcc: z@w"]}},
    {"smtp": {"host": "h", "port": 25, "security": "none", "from": "a@b", "to": ["nobody"]}},
    {"smtp": {"host": "h", "port": 25, "security": "none", "from": "a@b", "to": ["x@y"],
              "username": 3}},
    {"smtp": {"host": "h", "port": 25, "security": "none", "from": "a@b", "to": ["x@y"],
              "password": 5}},
    {"thresholds": {"disk_percent": 120}},
    {"thresholds": {"sustain_minutes": 0}},
    {"thresholds": {"ssh_failures": True}},
    {"thresholds": {"bogus": 5}},
    {"heartbeat_url": "http://example.com/ping"},
    {"heartbeat_url": "https://" + "a" * 2048},
    {"ssh_enabled": "yes"},
    {"surprise": 1},
])
def test_alerts_configure_rejects_bad_shapes(rig, args):
    a = rig.agent()
    assert err(call(a, "alerts.configure", **args)) == "invalid"
    assert not os.path.exists(rig.config)


def test_alerts_configure_persists_and_reloads(rig):
    a = rig.agent()
    res = ok(call(a, "alerts.configure", thresholds={"disk_percent": 80},
                  heartbeat_url="https://hc.example/ping", ssh_enabled=False))
    assert res["thresholds"]["disk_percent"] == 80 and res["ssh_enabled"] is False
    with open(rig.config, encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["alerts"]["heartbeat_url"] == "https://hc.example/ping"
    a.close()
    b = rig.agent()
    assert ok(call(b, "alerts.get"))["thresholds"]["disk_percent"] == 80


def test_corrupt_config_falls_back_to_defaults(rig, capsys):
    with open(rig.config, "w") as f:
        f.write("{not json")
    a = rig.agent()
    assert ok(call(a, "alerts.get"))["smtp"] is None
    assert "agent.json" in capsys.readouterr().err


def test_alerts_test_email_is_synchronous_and_reports_errors(rig):
    a = rig.agent()
    assert ok(call(a, "alerts.test_email")) == {"ok": True}
    assert len(rig.mailer.sent) == 1 and "测试邮件" in rig.mailer.sent[0][0]

    from panellab_agent.alerts import MailError

    def boom(subject, body):
        raise MailError("auth_failed", "邮件服务器认证失败，请检查用户名和密码")
    rig.mailer.send = boom
    res = ok(call(a, "alerts.test_email"))
    assert res == {"ok": False, "code": "auth_failed",
                   "message": "邮件服务器认证失败，请检查用户名和密码"}


def test_preview_returns_next_three_and_never_runs_invalid(rig, tmp_path):
    a = rig.agent()
    res = ok(call(a, "jobs.preview", schedule={"cron": ["0 9 * * *"], "tz": "Asia/Shanghai"}))
    runs = res["runs"]
    assert len(runs) == 3
    assert runs[0] == {"utc": "2026-11-11T01:00:00+00:00",
                       "local": "2026-11-11T09:00:00+08:00"}
    assert ok(call(a, "jobs.preview", schedule={"cron": []}))["runs"] == []
    never = {"cron": ["0 0 31 2 *"], "tz": "UTC"}
    resp = call(a, "jobs.preview", schedule=never)
    assert err(resp) == "invalid" and resp["error"]["message"] == "该计划永远不会运行"
    assert err(call(a, "jobs.create", job=job_def(tmp_path, schedule=never))) == "invalid"
    ok(call(a, "jobs.create", job=job_def(tmp_path)))
    assert err(call(a, "jobs.update", id="j", revision=1, changes={"schedule": never})) \
        == "invalid"
    assert err(call(a, "jobs.preview", schedule={"cron": ["99 * * * *"]})) == "invalid"


def test_pause_resume_delete_drop_retries_and_secrets(rig, tmp_path):
    a = rig.agent()
    ok(call(a, "jobs.create", job=job_def(tmp_path, command=["/bin/sh", "-c", "exit 1"],
                                          retries=2, retry_delay_seconds=30),
            secret_env={"K": "v"}))
    run_id = ok(call(a, "jobs.run_now", id="j"))["run_id"]
    wait_run(a, run_id)
    assert "j" in a.runner.running_job_ids()  # retry pending
    assert ok(call(a, "jobs.pause", id="j"))["paused"] is True
    assert "j" not in a.runner.running_job_ids()
    assert ok(call(a, "jobs.resume", id="j"))["paused"] is False
    ok(call(a, "jobs.delete", id="j"))
    assert err(call(a, "jobs.get", id="j")) == "not_found"
    assert a.secrets.job_env("j") == {}
    assert err(call(a, "jobs.delete", id="j")) == "not_found"


def test_run_now_while_running_is_conflict_and_cancel(rig, tmp_path):
    a = rig.agent()
    ok(call(a, "jobs.create", job=job_def(tmp_path, command=["/bin/sleep", "30"])))
    run_id = ok(call(a, "jobs.run_now", id="j"))["run_id"]
    assert err(call(a, "jobs.run_now", id="j")) == "conflict"
    st = ok(call(a, "status"))
    assert st["running"] == [{"run_id": run_id, "job_id": "j",
                              "started_at": T0.isoformat()}]
    assert ok(call(a, "runs.cancel", run_id=run_id)) == {"cancelled": True}
    assert wait_run(a, run_id)["status"] == "cancelled"
    assert err(call(a, "runs.cancel", run_id=run_id)) == "conflict"
    assert err(call(a, "runs.cancel", run_id=424242)) == "not_found"


def test_status_fields(rig):
    a = rig.agent()
    st = ok(call(a, "status"))
    for key in ("version", "protocol_version", "started_at", "hostname", "server_time",
                "timezone", "draining", "job_count", "running", "firing", "mail",
                "alerts_configured"):
        assert key in st, key
    assert st["protocol_version"] == 1 and st["hostname"] == "testhost"
    assert st["timezone"] == "Asia/Shanghai" and st["draining"] is False
    assert st["alerts_configured"] is False and st["firing"] == []
    a.alerts.observe("disk:/", True, "磁盘空间不足", "body", notify_firing=False)
    assert ok(call(a, "status"))["firing"] == [
        {"key": "disk:/", "since": T0.isoformat(), "message": "磁盘空间不足"}]


def test_drain_refuses_new_runs(rig, tmp_path):
    a = rig.agent()
    d = job_def(tmp_path, schedule={"cron": ["* * * * *"], "tz": "UTC"})
    ok(call(a, "jobs.create", job=d))
    res = ok(call(a, "drain"))
    assert res["draining"] is True and res["active_count"] == 0
    assert err(call(a, "jobs.run_now", id="j")) == "draining"
    for _ in range(2):  # ctl drain renews the 30 s lease while it waits
        rig.clock.advance(25)
        ok(call(a, "drain"))
    rig.clock.advance(10)
    a.tick()
    assert a.store.list_runs("j") == []
    assert ok(call(a, "status"))["draining"] is True


def test_drain_force_cancels_running(rig, tmp_path):
    a = rig.agent()
    ok(call(a, "jobs.create", job=job_def(tmp_path, command=["/bin/sleep", "30"],
                                          timeout_seconds=100)))
    run_id = ok(call(a, "jobs.run_now", id="j"))["run_id"]
    res = ok(call(a, "drain"))
    assert res["active_count"] == 1 and res["wait_seconds"] == 160
    ok(call(a, "drain", force=True))
    assert wait_run(a, run_id)["status"] == "cancelled"
    assert ok(call(a, "drain"))["active_count"] == 0


def test_unexpected_exception_is_internal_without_details(rig, capsys):
    a = rig.agent()

    def boom():
        raise RuntimeError("secret detail")
    a.store.list_jobs = boom
    resp = call(a, "jobs.list")
    assert resp == {"ok": False, "error": {"code": "internal", "message": "内部错误"}}
    assert "secret detail" in capsys.readouterr().err


def test_runs_list_validates_and_clamps(rig, tmp_path):
    a = rig.agent()
    ok(call(a, "jobs.create", job=job_def(tmp_path)))
    for _ in range(3):
        a.store.record_missed("j", T0 - timedelta(hours=1))
    assert len(ok(call(a, "runs.list", job_id="j", limit=0))["runs"]) == 1
    assert len(ok(call(a, "runs.list", job_id="j", status="missed"))["runs"]) == 3
    assert err(call(a, "runs.list", job_id="j", status="weird")) == "invalid"
    assert err(call(a, "runs.list", job_id="nope")) == "not_found"


@pytest.mark.parametrize("th", [
    {"disk_percent": 80, "disk_recover_percent": 80},
    {"disk_recover_percent": 95},                       # default trigger 90
    {"cpu_percent": 85, "recover_below_percent": 85},
    {"mem_percent": 80, "recover_below_percent": 90},   # >= mem trigger
    {"recover_below_percent": 96},                      # default triggers 95
])
def test_alerts_configure_rejects_recover_at_or_above_trigger(rig, th):
    a = rig.agent()
    assert err(call(a, "alerts.configure", thresholds=th)) == "invalid"
    assert not os.path.exists(rig.config)


def test_alerts_configure_accepts_recover_below_trigger(rig):
    a = rig.agent()
    ok(call(a, "alerts.configure", thresholds={"disk_percent": 80, "disk_recover_percent": 79,
                                               "cpu_percent": 85, "recover_below_percent": 84}))


def test_jobs_list_carries_last_run_and_next_run(rig, tmp_path):
    a = rig.agent()
    ok(call(a, "jobs.create", job=job_def(tmp_path, id="manual")))
    ok(call(a, "jobs.create", job=job_def(
        tmp_path, id="daily", schedule={"cron": ["0 9 * * *"], "tz": "Asia/Shanghai"})))
    ok(call(a, "jobs.create", job=job_def(
        tmp_path, id="stopped", schedule={"cron": ["0 9 * * *"], "tz": "UTC"})))
    ok(call(a, "jobs.pause", id="stopped"))
    jobs = {j["id"]: j for j in ok(call(a, "jobs.list"))["jobs"]}
    assert all(j["last_run"] is None for j in jobs.values())
    assert jobs["manual"]["next_run"] is None          # empty cron
    assert jobs["stopped"]["next_run"] is None         # paused
    assert jobs["daily"]["next_run"] == {"utc": "2026-11-11T01:00:00+00:00",
                                         "local": "2026-11-11T09:00:00+08:00"}
    # Same value jobs.preview gives for the job's own schedule.
    assert [jobs["daily"]["next_run"]] == ok(call(
        a, "jobs.preview", schedule=jobs["daily"]["schedule"]))["runs"][:1]

    st = a.store
    r1 = st.insert_run("daily", "schedule", None, 1)
    st.finish_run(r1, "succeeded", 0, 0, False)
    r2 = st.insert_run("daily", "manual", None, 1)
    st.finish_run(r2, "failed", 1, 0, False)
    r3 = st.insert_run("manual", "manual", None, 1)
    st.finish_run(r3, "cancelled", -15, 0, False)
    r4 = st.insert_run("stopped", "manual", None, 1)  # still running
    jobs = {j["id"]: j for j in ok(call(a, "jobs.list"))["jobs"]}
    keys = {"run_id", "status", "trigger", "started_at", "ended_at"}
    assert set(jobs["daily"]["last_run"]) == keys
    assert jobs["daily"]["last_run"]["run_id"] == r2
    assert jobs["daily"]["last_run"]["status"] == "failed"
    assert jobs["daily"]["last_run"]["trigger"] == "manual"
    assert jobs["daily"]["last_run"]["ended_at"] is not None
    assert jobs["manual"]["last_run"]["status"] == "cancelled"
    assert jobs["stopped"]["last_run"]["run_id"] == r4
    assert jobs["stopped"]["last_run"]["status"] == "running"
    assert jobs["stopped"]["last_run"]["ended_at"] is None
    # jobs.get stays a plain view.
    assert "last_run" not in ok(call(a, "jobs.get", id="daily"))


def test_version_is_0_1_1():
    from panellab_agent import __version__
    assert __version__ == "0.1.1"
