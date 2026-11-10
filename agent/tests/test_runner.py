import json
import os
import sys
import time
from datetime import datetime, timezone

import pytest

from panellab_agent.clock import FakeClock
from panellab_agent.runner import Runner
from panellab_agent.secret_store import Secrets
from panellab_agent.store import Store, StoreError

T0 = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


class Env:
    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.clock = FakeClock(T0)
        self.store = Store(str(tmp_path / "agent.db"), self.clock)
        self.secrets = Secrets(str(tmp_path / "secrets.json"))
        self.finished = []
        self.out = str(tmp_path / "out")
        os.makedirs(self.out)
        self.store.create_job({"id": "j", "name": "J", "command": ["/bin/true"],
                               "workdir": str(tmp_path)})

    def runner(self, **kw):
        return Runner(self.store, self.secrets, self.clock, self.out,
                      lambda job, run: self.finished.append((job, run)), **kw)

    def job(self, command, **over):
        job = self.store.get_job("j")
        job["command"] = command
        job.update(over)
        return job

    def wait(self, runner, run_id, advance=0.0, timeout=5.0):
        end = time.time() + timeout
        while time.time() < end:
            if advance:
                self.clock.advance(advance)
            runner.poll()
            if self.store.get_run(run_id)["status"] != "running":
                return self.store.get_run(run_id)
            time.sleep(0.02)
        raise AssertionError("run did not finish")

    def text(self, run_id):
        with open(os.path.join(self.out, "%d.log" % run_id), "rb") as f:
            return f.read().decode("utf-8", "replace")


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def py(code, *args):
    return [sys.executable, "-c", code] + list(args)


def test_args_with_spaces_quotes_chinese_and_subshell_are_literal(env):
    r = env.runner()
    args = ["a b", "中文", "'q'", "$(id)"]
    rid = r.start(env.job(py("import sys,json;print(json.dumps(sys.argv[1:]))", *args)),
                  "manual", None)
    run = env.wait(r, rid)
    assert run["status"] == "succeeded"
    assert json.loads(env.text(rid)) == args


def test_success_and_exit_code(env):
    r = env.runner()
    rid = r.start(env.job(["/bin/sh", "-c", "echo hi; exit 0"]), "manual", None)
    run = env.wait(r, rid)
    assert run["status"] == "succeeded" and run["exit_code"] == 0
    assert run["output_bytes"] == 3 and run["truncated"] is False
    rid = r.start(env.job(["/bin/sh", "-c", "echo bad >&2; exit 3"]), "manual", None)
    run = env.wait(r, rid)
    assert run["status"] == "failed" and run["exit_code"] == 3
    assert "bad" in env.text(rid)
    assert r.active_count() == 0 and r.running_job_ids() == set()


def test_timeout_kills_process_group_including_children(env):
    r = env.runner()
    rid = r.start(env.job(["/bin/sh", "-c", "sleep 60 & echo $!; wait"], timeout_seconds=10),
                  "manual", None)
    deadline = time.time() + 3
    while not env.text(rid).strip() and time.time() < deadline:
        time.sleep(0.02)
    pid = int(env.text(rid).strip())
    assert r.running_job_ids() == {"j"}
    run = env.wait(r, rid, advance=11)
    assert run["status"] == "timed_out"
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        raise AssertionError("child survived")
    assert len(env.finished) == 1


def test_timeout_escalates_to_sigkill_after_grace(env):
    r = env.runner(kill_grace_seconds=30)
    rid = r.start(env.job(["/bin/sh", "-c", "trap '' TERM; echo ready; while :; do sleep 0.1; done"],
                          timeout_seconds=10), "manual", None)
    while "ready" not in env.text(rid):
        time.sleep(0.02)
    env.clock.advance(11)
    r.poll()
    time.sleep(0.3)
    r.poll()
    assert env.store.get_run(rid)["status"] == "running"  # ignored SIGTERM
    env.clock.advance(31)
    run = env.wait(r, rid)
    assert run["status"] == "timed_out"


def test_output_truncated_at_limit(env):
    r = env.runner(output_limit=1024)
    rid = r.start(env.job(py("import sys;sys.stdout.write('x'*5000)")), "manual", None)
    run = env.wait(r, rid)
    assert run["status"] == "succeeded" and run["truncated"] is True
    text = env.text(rid)
    assert text.startswith("x" * 1024 + "\n")
    assert text.endswith("[输出超过 10 MiB，已截断]\n")
    assert run["output_bytes"] == os.path.getsize(os.path.join(env.out, "%d.log" % rid))
    assert r.read_output(rid, 0)["truncated"] is True


def test_invalid_utf8_output_reads_with_replacement(env):
    r = env.runner()
    rid = r.start(env.job(py("import sys;sys.stdout.buffer.write(b'ok \\xff\\xfe end')")),
                  "manual", None)
    env.wait(r, rid)
    res = r.read_output(rid, 0)
    assert res["data"] == "ok �� end"
    assert res["eof"] is True and res["offset"] == 9


def test_read_output_pages_without_splitting_utf8(env):
    r = env.runner()
    rid = r.start(env.job(py("print('中文' * 4)")), "manual", None)
    env.wait(r, rid)
    got, off = "", 0
    for _ in range(20):
        res = r.read_output(rid, off, limit=4)
        assert "�" not in res["data"]
        got += res["data"]
        off = res["offset"]
        if res["eof"]:
            break
    assert got == "中文" * 4 + "\n"
    with pytest.raises(StoreError) as e:
        r.read_output(9999, 0)
    assert e.value.code == "not_found"


def test_retry_then_success_reports_only_final(env):
    r = env.runner()
    cmd = ["/bin/sh", "-c", "if [ -f m ]; then exit 0; else touch m; exit 1; fi"]
    job = env.job(cmd, retries=1, retry_delay_seconds=30)
    rid = r.start(job, "schedule", T0)
    run = env.wait(r, rid)
    assert run["status"] == "failed"
    assert env.finished == []
    assert r.running_job_ids() == {"j"} and r.active_count() == 0
    r.poll()
    assert len(env.store.list_runs("j")) == 1  # not due yet
    env.clock.advance(30)
    r.poll()
    runs = env.store.list_runs("j")
    assert len(runs) == 2 and runs[0]["trigger"] == "retry" and runs[0]["attempt"] == 2
    assert runs[0]["scheduled_for"] == runs[1]["scheduled_for"]
    env.wait(r, runs[0]["id"])
    assert len(env.finished) == 1
    fjob, frun = env.finished[0]
    assert frun["status"] == "succeeded" and frun["attempt"] == 2 and fjob["id"] == "j"
    assert r.running_job_ids() == set()


def test_retries_exhausted_reports_failure_once(env):
    r = env.runner()
    job = env.job(["/bin/sh", "-c", "exit 1"], retries=1, retry_delay_seconds=5)
    rid = r.start(job, "manual", None)
    env.wait(r, rid)
    env.clock.advance(5)
    r.poll()
    rid2 = env.store.list_runs("j")[0]["id"]
    env.wait(r, rid2)
    assert len(env.finished) == 1 and env.finished[0][1]["status"] == "failed"
    assert env.finished[0][1]["attempt"] == 2 and r.running_job_ids() == set()


def test_missing_workdir_is_failed_with_reason(env):
    r = env.runner()
    rid = r.start(env.job(["/bin/true"], workdir="/nonexistent/x"), "manual", None)
    run = env.store.get_run(rid)
    assert run["status"] == "failed" and run["exit_code"] is None
    assert env.text(rid).strip() == "无法启动：工作目录不存在 /nonexistent/x"
    r.poll()
    assert len(env.finished) == 1


def test_command_not_found_and_bad_env_file(env):
    r = env.runner()
    rid = r.start(env.job(["/no/such/cmd"]), "manual", None)
    assert env.store.get_run(rid)["status"] == "failed"
    assert "无法启动" in env.text(rid)
    rid = r.start(env.job(["/bin/true"], env_file="/no/such/envfile"), "manual", None)
    assert env.store.get_run(rid)["status"] == "failed"
    assert "无法启动" in env.text(rid)


def test_env_layers_and_secret_not_logged(env, capsys):
    env.secrets.set_job_secrets("j", {"TOKEN": "s3cr3t-value", "LAYER": "secret"})
    ef = env.tmp / "job.env"
    ef.write_text("# c\nFROMFILE=f\nLAYER=file\nOVER=file\n\n")
    r = env.runner()
    code = "import os;print(os.environ['LAYER'],os.environ['OVER'],os.environ['FROMFILE'],os.environ['HOME'],os.environ['PATH'],os.environ['LANG'])"
    rid = r.start(env.job(py(code), env={"OVER": "env", "LAYER": "env"}, env_file=str(ef)),
                  "manual", None)
    env.wait(r, rid)
    assert env.text(rid).split() == ["secret", "env", "f", str(env.tmp),
                                     "/usr/local/bin:/usr/bin:/bin", "C.UTF-8"]
    rid = r.start(env.job(py("import os;print(len(os.environ['TOKEN']))")), "manual", None)
    env.wait(r, rid)
    assert env.text(rid).strip() == "12"
    assert "s3cr3t-value" not in env.text(rid)
    cap = capsys.readouterr()
    assert "s3cr3t-value" not in cap.out + cap.err
    assert "s3cr3t-value" not in json.dumps(env.store.get_run(rid))


def test_cancel_marks_cancelled(env):
    r = env.runner()
    rid = r.start(env.job(["/bin/sh", "-c", "sleep 60"]), "manual", None)
    assert r.cancel(rid) is True
    run = env.wait(r, rid)
    assert run["status"] == "cancelled"
    assert len(env.finished) == 1 and env.finished[0][1]["status"] == "cancelled"
    assert r.cancel(rid) is False


def test_cancel_prevents_retry_and_exit_not_failed(env):
    r = env.runner()
    job = env.job(["/bin/sh", "-c", "sleep 60"], retries=3)
    rid = r.start(job, "manual", None)
    r.cancel(rid)
    env.wait(r, rid)
    env.clock.advance(1000)
    r.poll()
    assert len(env.store.list_runs("j")) == 1 and r.running_job_ids() == set()


def test_only_running_runs_are_finished(env):
    r = env.runner()
    rid = r.start(env.job(["/bin/sh", "-c", "sleep 0.2; exit 1"]), "manual", None)
    env.store.finish_run(rid, "cancelled", None, 0, False)
    time.sleep(0.5)
    r.poll()
    time.sleep(0.1)
    r.poll()
    assert env.store.get_run(rid)["status"] == "cancelled"


def test_on_finish_exception_does_not_break_poll(env, capsys):
    def boom(job, run):
        raise RuntimeError("boom")
    r = Runner(env.store, env.secrets, env.clock, env.out, boom)
    rid = r.start(env.job(["/bin/true"]), "manual", None)
    env.wait(r, rid)
    r.poll()
    assert "boom" in capsys.readouterr().err


def test_delete_output(env):
    r = env.runner()
    rid = r.start(env.job(["/bin/echo", "x"]), "manual", None)
    env.wait(r, rid)
    r.delete_output([rid, 424242])
    assert not os.path.exists(os.path.join(env.out, "%d.log" % rid))
