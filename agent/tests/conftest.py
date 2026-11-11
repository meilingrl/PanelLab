"""Shared fixtures for the daemon/protocol tests (no real /proc, logs, network, /etc)."""
import os
import shutil
import tempfile
import threading
import time
from datetime import datetime, timezone

import pytest

from panellab_agent.clock import FakeClock

T0 = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


class FakeMailer:
    """Stands in for alerts.Mailer; optionally blocks until `release` is set."""

    def __init__(self, block=None):
        self.sent = []
        self.block = block
        self._last = {"at": None, "ok": False, "error": None}

    def send(self, subject, body):
        if self.block is not None:
            self.block.wait(30)
        self.sent.append((subject, body))
        self._last = {"at": "x", "ok": True, "error": None}

    def last_result(self):
        return dict(self._last)

    def subjects(self):
        return [s for s, _ in self.sent]


def quiet_readers(**over):
    readers = {
        "proc_stat": lambda: None,
        "meminfo": lambda: None,
        "mounts": lambda: "",
        "statvfs": os.statvfs,
        "ssh_lines": lambda since: [],
        "opener": None,
    }
    readers.update(over)
    return readers


class Rig:
    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.data = str(tmp_path / "data")
        self.config = str(tmp_path / "agent.json")
        self.clock = FakeClock(T0)
        self.mailer = FakeMailer()
        self.agents = []

    def agent(self, **kw):
        from panellab_agent.daemon import Agent
        kw.setdefault("mailer", self.mailer)
        kw.setdefault("hostname", "testhost")
        kw.setdefault("server_tz", "Asia/Shanghai")
        kw.setdefault("readers", quiet_readers())
        a = Agent(self.config, self.data, self.clock, **kw)
        self.agents.append(a)
        return a

    def store(self):
        """A Store on the rig's database, for seeding state before an Agent starts."""
        from panellab_agent.store import Store
        os.makedirs(self.data, exist_ok=True)
        return Store(os.path.join(self.data, "agent.db"), self.clock)

    def wait_mail(self, agent, timeout=5.0):
        assert agent.mail_queue.wait_idle(timeout)

    def close(self):
        for a in self.agents:
            if a.closed:
                continue
            for run in a.store.running_runs():
                a.runner.cancel(run["id"])
            end = time.time() + 5
            while a.runner.active_count() and time.time() < end:
                a.runner.poll()
                time.sleep(0.02)
            a.close()


@pytest.fixture
def rig(tmp_path):
    r = Rig(tmp_path)
    yield r
    r.close()


@pytest.fixture
def short_dir():
    """Unix socket paths must stay under ~104 bytes on macOS."""
    d = tempfile.mkdtemp(prefix="pa", dir="/tmp")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def call(agent, op, **args):
    from panellab_agent import protocol
    return protocol.handle(agent, {"protocol_version": 1, "op": op, "args": args})


def ok(resp):
    assert resp["ok"] is True, resp
    return resp["result"]


def err(resp):
    assert resp["ok"] is False, resp
    return resp["error"]["code"]


def wait_run(agent, run_id, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        agent.tick()
        run = agent.store.get_run(run_id)
        if run["status"] != "running":
            return run
        time.sleep(0.02)
    raise AssertionError("run did not finish")


def job_def(tmp_path, **over):
    job = {"id": "j", "name": "J", "command": ["/bin/sh", "-c", "echo hello"],
           "workdir": str(tmp_path), "schedule": {"cron": [], "tz": "UTC"}}
    job.update(over)
    return job


__all__ = ["FakeMailer", "quiet_readers", "call", "ok", "err", "wait_run", "job_def",
           "T0", "threading"]
