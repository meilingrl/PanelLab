import errno
import io
import json
import os
import socket
import threading
import time
from datetime import timedelta
from types import SimpleNamespace

import pytest

from conftest import T0, FakeMailer, call, job_def, ok, quiet_readers, wait_run
from panellab_agent import ctl, daemon


# ------------------------------------------------------------ startup recovery

def test_startup_marks_running_as_agent_lost_and_alerts(rig, tmp_path):
    s = rig.store()
    s.create_job(job_def(tmp_path, name="新闻简报"))
    rid = s.insert_run("j", "schedule", T0 - timedelta(minutes=1), 1)
    s.close()
    a = rig.agent()
    assert a.store.get_run(rid)["status"] == "agent_lost"
    assert a.store.get_alert("job:j")["state"] == "firing"
    rig.wait_mail(a)
    subjects = rig.mailer.subjects()
    assert any("新闻简报" in subj and "作业失败" in subj for subj in subjects), subjects


def test_startup_reports_downtime_over_5_minutes(rig):
    s = rig.store()
    s.set_meta("last_alive", (T0 - timedelta(minutes=10)).isoformat())
    s.close()
    a = rig.agent()
    rig.wait_mail(a)
    (subject, body), = rig.mailer.sent
    assert "Agent 曾停止运行" in subject
    assert (T0 - timedelta(minutes=10)).isoformat() in body and T0.isoformat() in body
    assert "20:00" in body  # local (Asia/Shanghai) time of T0


def test_startup_short_gap_is_quiet(rig):
    s = rig.store()
    s.set_meta("last_alive", (T0 - timedelta(minutes=4)).isoformat())
    s.close()
    a = rig.agent()
    rig.wait_mail(a)
    assert rig.mailer.sent == []


def test_startup_catch_up_uses_last_alive_and_notifies_lateness(rig, tmp_path):
    rig.clock.advance(-3 * 3600)
    s = rig.store()
    s.create_job(job_def(tmp_path, schedule={"cron": ["30 11 * * *"], "tz": "UTC"},
                         command=["/bin/true"]))
    s.create_job(job_def(tmp_path, id="k", name="K", catch_up_window_minutes=0,
                         schedule={"cron": ["30 11 * * *"], "tz": "UTC"}))
    s.set_meta("last_alive", (T0 - timedelta(minutes=50)).isoformat())
    s.close()
    rig.clock.advance(3 * 3600)
    a = rig.agent()
    a.tick()
    runs = a.store.list_runs("j")
    assert [r["trigger"] for r in runs] == ["catch_up"]
    missed = a.store.list_runs("k")
    assert [r["status"] for r in missed] == ["missed"]
    assert a.store.get_alert("job:k")["state"] == "firing"
    wait_run(a, runs[0]["id"])
    rig.wait_mail(a)
    subjects = rig.mailer.subjects()
    assert any("补跑：J 迟到 30 分钟" in s_ for s_ in subjects), subjects
    assert any("作业错过计划：K" in s_ for s_ in subjects), subjects
    assert a.store.get_meta("last_alive") == T0.isoformat()


# ------------------------------------------------------------------ tick wiring

def test_job_failure_alert_includes_tail_and_recovery(rig, tmp_path):
    a = rig.agent()
    script = "for i in $(seq 1 30); do echo line$i; done; exit ${FAIL:-0}"
    ok(call(a, "jobs.create", job=job_def(tmp_path, command=["/bin/sh", "-c", script],
                                          env={"FAIL": "3"})))
    wait_run(a, ok(call(a, "jobs.run_now", id="j"))["run_id"])
    rig.wait_mail(a)
    (subject, body), = rig.mailer.sent
    assert "作业失败：J" in subject
    assert "line30" in body and "line11" in body and "line10\n" not in body
    assert "退出码：3" in body
    ok(call(a, "jobs.update", id="j", revision=1, changes={"env": {}}))
    wait_run(a, ok(call(a, "jobs.run_now", id="j"))["run_id"])
    rig.wait_mail(a)
    assert "已恢复" in rig.mailer.sent[-1][0]
    assert a.store.get_alert("job:j")["state"] == "ok"


def test_timeout_subject_and_alert_flags(rig, tmp_path):
    a = rig.agent()
    ok(call(a, "jobs.create", job=job_def(
        tmp_path, command=["/bin/sh", "-c", "echo SECRETOUT; exit 1"],
        alert={"on_failure": True, "on_recovery": True, "include_output": False})))
    wait_run(a, ok(call(a, "jobs.run_now", id="j"))["run_id"])
    rig.wait_mail(a)
    assert "SECRETOUT" not in rig.mailer.sent[0][1]
    a.on_finish(a.store.get_job("j"), {"id": 1, "status": "timed_out", "trigger": "manual",
                                       "attempt": 1, "exit_code": None, "output_bytes": 0,
                                       "scheduled_for": None})
    a.store.put_alert("job:j", "ok", T0.isoformat(), None, "x")
    a.on_finish(a.store.get_job("j"), {"id": 1, "status": "timed_out", "trigger": "manual",
                                       "attempt": 1, "exit_code": None, "output_bytes": 0,
                                       "scheduled_for": None})
    rig.wait_mail(a)
    assert "作业超时：J" in rig.mailer.sent[-1][0]


def test_skipped_overlap_is_recorded(rig, tmp_path):
    a = rig.agent()
    ok(call(a, "jobs.create", job=job_def(tmp_path, command=["/bin/sleep", "30"],
                                          schedule={"cron": ["* * * * *"], "tz": "UTC"})))
    run_id = ok(call(a, "jobs.run_now", id="j"))["run_id"]
    rig.clock.advance(60)
    a.tick()
    statuses = [r["status"] for r in a.store.list_runs("j")]
    assert statuses == ["skipped_overlap", "running"]
    a.runner.cancel(run_id)


def test_schedule_errors_are_reported(rig, tmp_path, capsys):
    a = rig.agent()
    ok(call(a, "jobs.create", job=job_def(tmp_path)))
    real = a.store.list_jobs

    def broken():
        jobs = real()
        jobs[0]["schedule"] = {"cron": ["bad"], "tz": "UTC"}
        return jobs
    a.store.list_jobs = broken
    a.tick()
    a.tick()
    assert a.store.get_alert("job:j:schedule")["state"] == "firing"
    assert a.store.get_alert("job:j:schedule")["message"] == "作业计划无效：j"
    assert a.store.get_alert("job:j") is None  # failure/recovery key untouched
    assert capsys.readouterr().err.count("invalid schedule") == 1
    rig.wait_mail(a)
    sent = len(rig.mailer.sent)
    a.store.list_jobs = real  # schedule valid again on a later tick
    a.tick()
    assert a.store.get_alert("job:j:schedule")["state"] == "ok"
    rig.wait_mail(a)
    assert len(rig.mailer.sent) == sent  # cleared without a recovery mail


def test_update_clears_schedule_alert(rig, tmp_path):
    a = rig.agent()
    ok(call(a, "jobs.create", job=job_def(tmp_path)))
    a.alerts.observe("job:j:schedule", True, "作业计划无效：j", "x", notify_firing=False)
    ok(call(a, "jobs.update", id="j", revision=1,
            changes={"schedule": {"cron": ["0 9 * * *"], "tz": "UTC"}}))
    assert a.store.get_alert("job:j:schedule")["state"] == "ok"
    rig.wait_mail(a)
    assert rig.mailer.sent == []


def test_second_agent_on_same_data_dir_refused_before_recovery(rig, tmp_path):
    a = rig.agent()
    ok(call(a, "jobs.create", job=job_def(tmp_path, command=["/bin/sleep", "30"])))
    run_id = ok(call(a, "jobs.run_now", id="j"))["run_id"]
    with pytest.raises(daemon.AgentAlreadyRunning) as exc:
        rig.agent()
    assert str(exc.value) == "另一个 Agent 正在运行"
    assert a.store.get_run(run_id)["status"] == "running"
    rig.wait_mail(a)
    assert rig.mailer.sent == []


def test_main_exits_nonzero_when_data_dir_locked(rig, short_dir, capsys):
    a = rig.agent()
    code = daemon.main(["--config", rig.config, "--data-dir", rig.data,
                        "--socket", os.path.join(short_dir, "m.sock")])
    assert code != 0 and "另一个 Agent 正在运行" in capsys.readouterr().err
    assert not os.path.exists(os.path.join(short_dir, "m.sock"))
    assert not a.closed


def test_make_server_refuses_live_socket(rig, short_dir):
    path = os.path.join(short_dir, "live.sock")
    live = socket.socket(socket.AF_UNIX)
    live.bind(path)
    live.listen(1)
    try:
        with pytest.raises(daemon.AgentAlreadyRunning):
            daemon.make_server(rig.agent(), path)
        assert os.path.exists(path)
    finally:
        live.close()


def test_queued_mailer_close_drains_queue():
    class Slow:
        def __init__(self):
            self.sent = []

        def send(self, s, b):
            time.sleep(0.05)
            self.sent.append(s)

        def last_result(self):
            return {}
    inner = Slow()
    q = daemon.QueuedMailer(inner)
    for i in range(3):
        q.send("s%d" % i, "b")
    assert q.close(timeout=5) is True
    assert inner.sent == ["s0", "s1", "s2"]
    blocked = daemon.QueuedMailer(FakeMailer(block=threading.Event()))
    blocked.send("s", "b")
    t = time.monotonic()
    assert blocked.close(timeout=0.2) is False
    assert time.monotonic() - t < 2


def test_watchers_feed_alerts_on_cadence(rig):
    used = {"pct": 95}

    def statvfs(path):
        return SimpleNamespace(f_blocks=100, f_bavail=100 - used["pct"])
    stat_lines = iter(["cpu 0 0 0 100 0", "cpu 100 0 0 100 0", "cpu 200 0 0 100 0"])
    readers = quiet_readers(mounts=lambda: "/dev/sda1 / ext4 rw 0 0\n", statvfs=statvfs,
                            proc_stat=lambda: next(stat_lines),
                            meminfo=lambda: "MemTotal: 100 kB\nMemAvailable: 50 kB\n")
    a = rig.agent(readers=readers)
    a.tick()
    assert a.store.get_alert("disk:/")["state"] == "firing"
    used["pct"] = 10
    rig.clock.advance(30)
    a.tick()  # disk not re-checked until 60 s
    assert a.store.get_alert("disk:/")["state"] == "firing"
    rig.clock.advance(30)
    a.tick()
    assert a.store.get_alert("disk:/")["state"] == "ok"
    assert a.cpu_watch._above_since is not None  # 100% busy samples arrived


def test_ssh_alert_survives_restart_and_recovers_after_quiet_period(rig):
    st = rig.store()
    st.put_alert("ssh", "firing", T0.isoformat(), T0.isoformat(),
                 "SSH 登录失败激增：10 分钟内 80 次")
    st.close()
    a = rig.agent()
    a.tick()
    assert a.ssh_watch.firing is True
    assert a.store.get_alert("ssh")["state"] == "firing"
    for _ in range(29):
        rig.clock.advance(60)
        a.tick()
    assert a.store.get_alert("ssh")["state"] == "firing"
    rig.clock.advance(60)
    a.tick()
    assert a.store.get_alert("ssh")["state"] == "ok"
    rig.wait_mail(a)
    assert any("已恢复：SSH 登录失败激增" in s for s in rig.mailer.subjects())


def test_failing_phase_does_not_stop_the_tick(rig, capsys):
    def boom():
        raise RuntimeError("mounts exploded")
    a = rig.agent(readers=quiet_readers(mounts=boom))
    rig.clock.advance(5)
    a.tick()
    assert a.store.get_meta("last_alive") == (T0 + timedelta(seconds=5)).isoformat()
    assert "mounts exploded" in capsys.readouterr().err


def test_smtp_hang_does_not_block_tick(rig, tmp_path):
    release = threading.Event()
    rig.mailer = FakeMailer(block=release)
    s = rig.store()
    s.create_job(job_def(tmp_path, schedule={"cron": ["* * * * *"], "tz": "UTC"},
                         catch_up_window_minutes=0))
    s.insert_run("j", "schedule", T0, 1)
    s.close()
    t = time.monotonic()
    a = rig.agent()  # agent_lost alert is queued; the mailer hangs in the background
    rig.clock.advance(3600)  # 60 missed slots -> more alerts
    a.tick()
    a.alerts.notify("x", "y")
    assert time.monotonic() - t < 2.0
    assert rig.mailer.sent == []
    release.set()
    rig.wait_mail(a)
    assert rig.mailer.sent


def test_queued_mailer_swallows_errors(capsys):
    class Bad:
        def send(self, s, b):
            raise ValueError("password=hunter2")

        def last_result(self):
            return {}
    q = daemon.QueuedMailer(Bad())
    q.send("s", "b")
    assert q.wait_idle(5)
    err_text = capsys.readouterr().err
    assert "hunter2" not in err_text and "panellab-agent" in err_text
    q.close()


def test_server_timezone_detection(tmp_path):
    etc_tz = tmp_path / "timezone"
    etc_tz.write_text("Europe/Berlin\n")
    assert daemon.server_timezone({"TZ": "Asia/Tokyo"}, str(etc_tz), "/nope") == "Asia/Tokyo"
    assert daemon.server_timezone({"TZ": "Bad/Zone"}, str(etc_tz), "/nope") == "Europe/Berlin"
    link = tmp_path / "localtime"
    os.symlink("/usr/share/zoneinfo/America/New_York", str(link))
    assert daemon.server_timezone({}, "/nope", str(link)) == "America/New_York"
    assert daemon.server_timezone({}, "/nope", "/nope") == "UTC"


# ----------------------------------------------------------------- socket + ctl

def _serve(agent, path):
    server = daemon.make_server(agent, path)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05},
                     daemon=True).start()
    return server


def _ctl(monkeypatch, argv, stdin=""):
    out, errb = io.StringIO(), io.StringIO()
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    monkeypatch.setattr("sys.stdout", out)
    monkeypatch.setattr("sys.stderr", errb)
    code = ctl.main(argv)
    return code, out.getvalue(), errb.getvalue()


def test_socket_roundtrip_via_ctl(rig, short_dir, monkeypatch):
    a = rig.agent()
    path = os.path.join(short_dir, "a.sock")
    stale = socket.socket(socket.AF_UNIX)
    stale.bind(path)  # stale socket file from a previous run is replaced
    stale.close()
    server = _serve(a, path)
    try:
        assert (os.stat(path).st_mode & 0o777) == 0o660
        req = json.dumps({"protocol_version": 1, "op": "status", "args": {}})
        code, out, _ = _ctl(monkeypatch, ["--socket", path], req + "\n")
        assert code == 0 and out.endswith("\n") and out.count("\n") == 1
        resp = json.loads(out)
        assert resp["ok"] is True and resp["result"]["hostname"] == "testhost"
        code, out, _ = _ctl(monkeypatch, ["--socket", path],
                            json.dumps({"protocol_version": 9, "op": "status"}) + "\n")
        assert code == 0 and json.loads(out)["error"]["code"] == "protocol_mismatch"
        code, out, errt = _ctl(monkeypatch, ["--socket", path], "{oops\n")
        assert code == 2 and out == ""
        code, out, errt = _ctl(monkeypatch, ["--socket", path, "drain"])
        assert code == 0 and json.loads(out)["result"]["active_count"] == 0
        assert a.draining
        # raw socket: garbage and oversize lines get bad_request
        for payload in (b"nope\n", b"x" * (1024 * 1024 + 1)):
            c = socket.socket(socket.AF_UNIX)
            c.connect(path)
            c.sendall(payload)
            data = b""
            while not data.endswith(b"\n"):
                chunk = c.recv(65536)
                if not chunk:
                    break
                data += chunk
            c.close()
            assert json.loads(data)["error"]["code"] == "bad_request"
    finally:
        server.shutdown()
        server.server_close()


def test_ctl_exit_codes_unreachable_3_permission_4(short_dir, monkeypatch):
    req = json.dumps({"protocol_version": 1, "op": "status", "args": {}}) + "\n"
    missing = os.path.join(short_dir, "missing.sock")
    code, out, errt = _ctl(monkeypatch, ["--socket", missing], req)
    assert code == 3 and json.loads(errt)["code"] == "agent_unreachable" and out == ""
    refused = os.path.join(short_dir, "dead.sock")
    s = socket.socket(socket.AF_UNIX)
    s.bind(refused)
    s.close()  # file left behind, nobody listening
    code, _, errt = _ctl(monkeypatch, ["--socket", refused], req)
    assert code == 3 and json.loads(errt)["code"] == "agent_unreachable"

    def denied(path):
        raise PermissionError(errno.EACCES, "Permission denied")
    monkeypatch.setattr(ctl, "_connect", denied)
    code, _, errt = _ctl(monkeypatch, ["--socket", refused], req)
    assert code == 4 and json.loads(errt)["code"] == "agent_permission"
    code, _, _ = _ctl(monkeypatch, ["--bogus"])
    assert code == 2


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores socket permissions")
def test_ctl_permission_on_real_socket(rig, short_dir, monkeypatch):
    a = rig.agent()
    path = os.path.join(short_dir, "p.sock")
    server = _serve(a, path)
    try:
        os.chmod(path, 0)
        req = json.dumps({"protocol_version": 1, "op": "status", "args": {}}) + "\n"
        code, _, errt = _ctl(monkeypatch, ["--socket", path], req)
        assert code == 4 and json.loads(errt)["code"] == "agent_permission"
    finally:
        server.shutdown()
        server.server_close()


def test_ctl_drain_waits_for_running_to_finish(rig, short_dir, monkeypatch, tmp_path):
    a = rig.agent()
    ok(call(a, "jobs.create", job=job_def(tmp_path, command=["/bin/sleep", "30"])))
    ok(call(a, "jobs.run_now", id="j"))
    path = os.path.join(short_dir, "d.sock")
    server = _serve(a, path)
    stop = threading.Event()

    def ticker():
        while not stop.is_set():
            a.tick()
            time.sleep(0.02)
    threading.Thread(target=ticker, daemon=True).start()
    monkeypatch.setattr(ctl, "POLL_SECONDS", 0.05)
    try:
        code, out, errt = _ctl(monkeypatch, ["--socket", path, "drain", "--force"])
        assert code == 0 and a.runner.active_count() == 0
        assert "排空" in errt
        assert a.store.list_runs("j")[0]["status"] == "cancelled"
    finally:
        stop.set()
        server.shutdown()
        server.server_close()


def test_ctl_times_out_on_silent_agent(short_dir, monkeypatch):
    path = os.path.join(short_dir, "silent.sock")
    silent = socket.socket(socket.AF_UNIX)
    silent.bind(path)
    silent.listen(1)  # accepts into the backlog, never replies
    monkeypatch.setattr(ctl, "TIMEOUT_SECONDS", 0.2)
    try:
        t = time.monotonic()
        req = json.dumps({"protocol_version": 1, "op": "status", "args": {}}) + "\n"
        code, out, errt = _ctl(monkeypatch, ["--socket", path], req)
        assert code == 3 and json.loads(errt)["code"] == "agent_unreachable" and out == ""
        assert time.monotonic() - t < 3
    finally:
        silent.close()


def test_ctl_default_timeout_is_120s():
    assert ctl.TIMEOUT_SECONDS == 120


# ------------------------------------------------------- R22: SIGTERM shutdown

def _step_until_done(a, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        a.tick()
        if a.shutdown_step():
            return True
        time.sleep(0.02)
    return False


def test_sigterm_cancels_long_job_after_grace_with_one_notice(rig, tmp_path):
    a = rig.agent()
    ok(call(a, "jobs.create", job=job_def(tmp_path, name="长作业", command=["/bin/sleep", "300"])))
    ok(call(a, "jobs.create", job=job_def(tmp_path, id="k", name="K",
                                          schedule={"cron": ["* * * * *"], "tz": "UTC"})))
    rid = ok(call(a, "jobs.run_now", id="j"))["run_id"]
    a.begin_shutdown()
    assert a.draining
    a.tick()
    assert a.shutdown_step() is False
    rig.clock.advance(59)
    a.tick()
    assert a.shutdown_step() is False
    assert a.store.get_run(rid)["status"] == "running"
    rig.clock.advance(2)
    assert _step_until_done(a)
    run = a.store.get_run(rid)
    assert run["status"] == "cancelled"
    out = a.runner.read_output(rid, 0, 65536)["data"]
    assert out.rstrip().endswith("[Agent 停止，作业被中断]")
    assert a.store.list_runs("k") == []  # nothing scheduled while stopping
    rig.wait_mail(a)
    notices = [(s, b) for s, b in rig.mailer.sent if "Agent 停止时中断了作业" in s]
    assert len(notices) == 1
    assert "长作业" in notices[0][1] and "#%d" % rid in notices[0][1]
    assert not any("作业失败" in s for s in rig.mailer.subjects()), rig.mailer.subjects()
    assert a.shutdown_step() is True  # the notice is sent once
    rig.wait_mail(a)
    assert sum("Agent 停止时中断了作业" in s for s in rig.mailer.subjects()) == 1


def test_sigterm_lets_short_jobs_finish_without_notice(rig, tmp_path):
    a = rig.agent()
    ok(call(a, "jobs.create", job=job_def(tmp_path, command=["/bin/sh", "-c", "sleep 0.3"])))
    rid = ok(call(a, "jobs.run_now", id="j"))["run_id"]
    a.begin_shutdown()
    assert _step_until_done(a)
    assert a.store.get_run(rid)["status"] == "succeeded"
    rig.wait_mail(a)
    assert not any("中断" in s for s in rig.mailer.subjects())


# ------------------------------------------------------------ R23: drain lease

def test_drain_lease_expiry_undrains_and_notifies(rig, tmp_path, capsys):
    a = rig.agent()
    ok(call(a, "drain"))
    rig.clock.advance(29)
    a.tick()
    assert a.draining
    rig.clock.advance(2)
    a.tick()
    assert not a.draining
    assert ok(call(a, "status"))["draining"] is False
    assert "drain lease expired" in capsys.readouterr().err
    rig.wait_mail(a)
    (subject, body), = [(s, b) for s, b in rig.mailer.sent if "Agent 已退出排空状态" in s]
    assert "排空未完成（安装/升级可能中断），已恢复调度" in body
    rig.clock.advance(60)
    a.tick()
    rig.wait_mail(a)
    assert sum("已退出排空状态" in s for s in rig.mailer.subjects()) == 1


def test_drain_renewal_keeps_draining(rig):
    a = rig.agent()
    ok(call(a, "drain"))
    for _ in range(6):
        rig.clock.advance(10)
        ok(call(a, "drain"))
        a.tick()
        assert a.draining
    rig.wait_mail(a)
    assert not any("排空" in s for s in rig.mailer.subjects())


def test_undrain_op_resumes_scheduling_without_notice(rig, tmp_path):
    a = rig.agent()
    ok(call(a, "jobs.create", job=job_def(tmp_path, command=["/bin/true"],
                                          schedule={"cron": ["* * * * *"], "tz": "UTC"})))
    ok(call(a, "drain"))
    rig.clock.advance(20)
    a.tick()  # 12:00:20, nothing due yet; last_scheduled stays at 12:00:00
    assert a.store.get_meta("last_scheduled") is None or \
        a.store.get_meta("last_scheduled") <= T0.isoformat()
    res = ok(call(a, "undrain"))
    assert res["draining"] is False and not a.draining
    rig.clock.advance(45)
    a.tick()  # 12:01:05: the 12:01 slot starts normally
    assert [r["trigger"] for r in a.store.list_runs("j")] == ["schedule"]
    rig.wait_mail(a)
    assert not any("排空" in s for s in rig.mailer.subjects())


def test_sigterm_drain_has_no_lease(rig):
    a = rig.agent()
    a.begin_shutdown()
    ok(call(a, "drain"))
    ok(call(a, "undrain"))
    rig.clock.advance(120)
    a.tick()
    assert a.draining


def test_ctl_undrain(rig, short_dir, monkeypatch):
    a = rig.agent()
    path = os.path.join(short_dir, "u.sock")
    server = _serve(a, path)
    try:
        ok(call(a, "drain"))
        code, out, _ = _ctl(monkeypatch, ["--socket", path, "undrain"])
        assert code == 0 and json.loads(out)["result"]["draining"] is False
        assert not a.draining
    finally:
        server.shutdown()
        server.server_close()


# ------------------------------------------------- I4: firing state after restart

def test_startup_seeds_resource_watches_from_firing_alerts(rig):
    s = rig.store()
    for key in ("cpu", "mem", "disk:/data", "disk:/gone", "job:x"):
        s.put_alert(key, "firing", T0.isoformat(), T0.isoformat(), key)
    s.close()
    readers = quiet_readers(mounts=lambda: "/dev/sda1 /data ext4 rw 0 0\n",
                            statvfs=lambda p: SimpleNamespace(f_blocks=100, f_bavail=90),
                            meminfo=lambda: "MemTotal: 100 kB\nMemAvailable: 90 kB\n")
    a = rig.agent(readers=readers)
    assert a.cpu_watch.firing is True and a.mem_watch.firing is True
    a.tick()
    assert a.store.get_alert("disk:/data")["state"] == "ok"
    assert a.store.get_alert("disk:/gone")["state"] == "ok"
    for _ in range(11):  # memory at 10% for 5 minutes recovers
        rig.clock.advance(30)
        a.tick()
    assert a.store.get_alert("mem")["state"] == "ok"
    assert a.store.get_alert("cpu")["state"] == "firing"  # no CPU samples yet


# ------------------------------------------------------- R24: deleted job ids

def test_delete_recreate_and_prune_deleted_runs(rig, tmp_path):
    a = rig.agent()
    ok(call(a, "jobs.create", job=job_def(tmp_path)))
    rid = ok(call(a, "jobs.run_now", id="j"))["run_id"]
    wait_run(a, rid)
    ok(call(a, "jobs.delete", id="j"))
    ok(call(a, "jobs.create", job=job_def(tmp_path, name="J2")))
    assert ok(call(a, "runs.list", job_id="j"))["runs"] == []
    assert "hello" in ok(call(a, "runs.output", run_id=rid))["data"]
    log = os.path.join(a.output_dir, "%d.log" % rid)
    assert os.path.exists(log)
    (tomb,) = a.store.deleted_job_ids()
    a.store.prune = lambda job_id: [rid] if job_id == tomb else []
    a._prune()
    assert not os.path.exists(log)


def test_run_of_deleted_job_does_not_alert_for_recreated_job(rig, tmp_path):
    a = rig.agent()
    ok(call(a, "jobs.create", job=job_def(tmp_path, command=["/bin/sh", "-c", "sleep 0.3; exit 1"])))
    rid = ok(call(a, "jobs.run_now", id="j"))["run_id"]
    ok(call(a, "jobs.delete", id="j"))
    rig.clock.advance(1)
    ok(call(a, "jobs.create", job=job_def(tmp_path)))
    assert wait_run(a, rid)["status"] == "failed"
    rig.wait_mail(a)
    assert not any("作业失败" in s for s in rig.mailer.subjects())
    assert (a.store.get_alert("job:j") or {}).get("state") != "firing"


# ------------------------------------------------------------- minor fixes

def test_m1_drain_arriving_during_start_loop_loses_no_due_time(rig, tmp_path):
    a = rig.agent()
    for jid in ("a1", "a2"):
        ok(call(a, "jobs.create", job=job_def(tmp_path, id=jid, name=jid, command=["/bin/true"],
                                              schedule={"cron": ["* * * * *"], "tz": "UTC"})))
    real_start = a.runner.start
    drainer = []

    def start(job, trigger, scheduled_for, attempt=1):
        if not drainer:  # a drain request arrives from the control socket mid-loop
            t = threading.Thread(target=a.drain)
            t.start()
            t.join(0.2)
            drainer.append(t)
        return real_start(job, trigger, scheduled_for, attempt)
    a.runner.start = start
    rig.clock.advance(60)
    a.tick()
    drainer[0].join(5)
    started = sorted(r["job_id"] for jid in ("a1", "a2") for r in a.store.list_runs(jid))
    assert started == ["a1", "a2"]  # the tick was applied as a whole
    assert a.store.get_meta("last_scheduled") == (T0 + timedelta(seconds=60)).isoformat()
    assert a.draining


def test_m1_drain_before_start_loop_does_not_advance(rig, tmp_path):
    a = rig.agent()
    ok(call(a, "jobs.create", job=job_def(tmp_path, command=["/bin/true"],
                                          schedule={"cron": ["* * * * *"], "tz": "UTC"})))
    real_plan = daemon.plan_tick

    def plan(*args, **kw):
        dec = real_plan(*args, **kw)
        a.drain()  # drain lands after planning but before anything starts
        return dec
    daemon.plan_tick = plan
    try:
        rig.clock.advance(60)
        a.tick()
    finally:
        daemon.plan_tick = real_plan
    assert a.store.list_runs("j") == []
    assert a.store.get_meta("last_scheduled") is None
    ok(call(a, "undrain"))
    rig.clock.advance(1)
    a.tick()
    assert [r["trigger"] for r in a.store.list_runs("j")] == ["schedule"]


def test_m2_pause_with_failed_retry_pending_alerts(rig, tmp_path):
    a = rig.agent()
    ok(call(a, "jobs.create", job=job_def(tmp_path, command=["/bin/sh", "-c", "exit 1"],
                                          retries=2, retry_delay_seconds=30)))
    wait_run(a, ok(call(a, "jobs.run_now", id="j"))["run_id"])
    rig.wait_mail(a)
    assert rig.mailer.sent == []
    ok(call(a, "jobs.pause", id="j"))
    rig.wait_mail(a)
    assert any("作业失败：J" in s for s in rig.mailer.subjects())


def test_m5_catch_up_notice_respects_on_failure(rig, tmp_path):
    rig.clock.advance(-3 * 3600)
    s = rig.store()
    s.create_job(job_def(tmp_path, schedule={"cron": ["30 11 * * *"], "tz": "UTC"},
                         command=["/bin/true"],
                         alert={"on_failure": False, "on_recovery": True, "include_output": True}))
    s.set_meta("last_alive", (T0 - timedelta(minutes=50)).isoformat())
    s.close()
    rig.clock.advance(3 * 3600)
    a = rig.agent()
    a.tick()
    runs = a.store.list_runs("j")
    assert [r["trigger"] for r in runs] == ["catch_up"]
    wait_run(a, runs[0]["id"])
    rig.wait_mail(a)
    assert not any("补跑" in s_ for s_ in rig.mailer.subjects())


def test_m6_status_reports_heartbeat(rig):
    a = rig.agent()
    assert ok(call(a, "status"))["heartbeat"] == {"configured": False, "at": None, "ok": None,
                                                  "error": None}

    def boom(req, timeout):
        raise OSError("down")
    a.heartbeat._opener = boom
    ok(call(a, "alerts.configure", heartbeat_url="https://hc.example.com/ping"))
    a.heartbeat.tick()
    hb = ok(call(a, "status"))["heartbeat"]
    assert hb["configured"] is True and hb["ok"] is False and hb["at"] == T0.isoformat()
    assert hb["error"] == "心跳请求失败" and "hc.example.com" not in json.dumps(hb)
