#!/usr/bin/env python3
"""Install the PanelLab Agent with install.sh into a disposable systemd container and check it.

Build (done automatically): docker build -t panellab-agent-lab:20261110 scripts/fixtures/agent_systemd
Run: python3 scripts/verify_agent_lab.py [--keep]
     .venv311/bin/python scripts/verify_agent_lab.py --panel   (needs the backend requirements)

--panel drives the same fixture through PanelLab's own backend API instead: a backend from
backend/ is started with this interpreter on a random loopback port with a temporary SQLite
database and its own SECRET_KEY, logs in to the fixture over SSH as admin (fixture-only
password; sshd is published only on a random 127.0.0.1 port), pins the host key, uploads the
bundle, and the returned sudo command is run inside the container as admin (standing in for
the user typing it). Its database is a disposable MySQL container, panellab-agent-lab-mysql
(tmpfs, 127.0.0.1 random port; the models need MySQL for BIGINT autoincrement ids). The
backend, that container and the temp directory are removed on exit.

Everything runs through `docker exec` as the fixture's `admin` user, with `sudo -n`
where root is needed (admin has NOPASSWD sudo in this fixture only). Mail goes to the
fixture's SMTP sink on 127.0.0.1:2525, which stores each message in /var/mail-sink/.

Only the container named panellab-agent-lab is created and removed here (also on Ctrl-C);
no other containers, images, networks or volumes are touched. Never points at a real server.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import signal
import subprocess
import sys
import os
import secrets
import socket
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "agent"
FIXTURE = ROOT / "scripts" / "fixtures" / "agent_systemd"
IMAGE = "panellab-agent-lab:20261110"
CONTAINER = "panellab-agent-lab"
MYSQL_CONTAINER = "panellab-agent-lab-mysql"
MYSQL_IMAGE = "mysql:8.4.9"
HOSTNAME = "agent-lab"
SERVICE = "panellab-agent.service"
RELOGIN_NOTE = "请重新登录 SSH 使组权限生效"
UPGRADE_VERSION = "0.1.1-lab"
FIXTURE_PASSWORD = "panellab-fixture-only"  # set in the fixture's Dockerfile; fixture only

READ_MAIL = r"""
import email, email.policy, json, os
out = []
d = "/var/mail-sink"
for name in sorted(os.listdir(d), key=lambda n: int(n.split(".")[0]) if n.split(".")[0].isdigit() else 0):
    if not name.endswith(".eml"):
        continue
    with open(os.path.join(d, name), "rb") as f:
        msg = email.message_from_binary_file(f, policy=email.policy.default)
    body = msg.get_body(preferencelist=("plain",))
    out.append({"file": name, "subject": str(msg["subject"] or ""),
                "body": body.get_content() if body is not None else ""})
print(json.dumps(out, ensure_ascii=False))
"""


class LabError(Exception):
    pass


def run(cmd: List[str], *, input: Optional[str] = None, timeout: float = 120,
        check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, input=input, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        raise LabError("%s failed (%d): %s" % (" ".join(cmd[:6]), result.returncode,
                                               (result.stderr or result.stdout).strip()[-1500:]))
    return result


def dexec(*cmd: str, user: str = "admin", input: Optional[str] = None, workdir: Optional[str] = None,
          timeout: float = 120, check: bool = True) -> subprocess.CompletedProcess:
    args = ["docker", "exec"]
    if input is not None:
        args.append("-i")
    args += ["-u", user]
    if workdir:
        args += ["-w", workdir]
    args.append(CONTAINER)
    return run(args + list(cmd), input=input, timeout=timeout, check=check)


def root(*cmd: str, timeout: float = 120, check: bool = True) -> subprocess.CompletedProcess:
    """Run as root the way an operator would: admin + sudo -n."""
    return dexec("sudo", "-n", *cmd, timeout=timeout, check=check)


def remove_container() -> None:
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True, timeout=60)


def wait_for(what: str, fn: Callable[[], Any], timeout: float = 30, interval: float = 0.5) -> Any:
    deadline = time.monotonic() + timeout
    last_exc: Optional[BaseException] = None
    while time.monotonic() < deadline:
        try:
            value = fn()
            if value:
                return value
        except (LabError, ValueError, KeyError) as exc:
            last_exc = exc
        time.sleep(interval)
    raise LabError("timed out waiting for %s%s" % (what, ": %s" % last_exc if last_exc else ""))


class Lab:
    def __init__(self) -> None:
        self.failures: List[str] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        if ok:
            print("PASS %s" % name, flush=True)
        else:
            self.failures.append(name)
            print("FAIL %s%s" % (name, " -- %s" % detail if detail else ""), flush=True)
        return ok

    # ---------------------------------------------------------------- agent
    def ctl(self, op: str, args: Optional[Dict[str, Any]] = None, user: str = "admin") -> Dict[str, Any]:
        line = json.dumps({"protocol_version": 1, "op": op, "args": args or {}})
        result = dexec("panellab-agentctl", user=user, input=line + "\n")
        resp = json.loads(result.stdout)
        if not resp.get("ok"):
            raise LabError("%s -> %s" % (op, resp.get("error")))
        return resp["result"]

    def runs(self, job_id: str) -> List[Dict[str, Any]]:
        return self.ctl("runs.list", {"job_id": job_id})["runs"]

    def run_by_id(self, job_id: str, run_id: int) -> Optional[Dict[str, Any]]:
        for r in self.runs(job_id):
            if r["id"] == run_id:
                return r
        return None

    def wait_run_done(self, job_id: str, run_id: int, timeout: float = 30) -> Dict[str, Any]:
        def done():
            r = self.run_by_id(job_id, run_id)
            return r if r and r["status"] != "running" else None
        return wait_for("run %s of %s to finish" % (run_id, job_id), done, timeout)

    def mails(self) -> List[Dict[str, str]]:
        return json.loads(dexec("python3", "-c", READ_MAIL).stdout)

    def wait_mail(self, pattern: str, after: int, timeout: float = 30) -> Dict[str, str]:
        rx = re.compile(pattern)

        def found():
            for m in self.mails()[after:]:
                if rx.search(m["subject"]):
                    return m
            return None
        return wait_for("mail matching %r" % pattern, found, timeout)

    # Without dbus in the fixture only root reaches systemd (via its private socket).
    def unit(self, *props: str) -> Dict[str, str]:
        out = root("systemctl", "show", SERVICE, *["-p%s" % p for p in props]).stdout
        return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)

    def main_pid(self) -> int:
        return int(self.unit("MainPID")["MainPID"])

    def is_active(self) -> str:
        return root("systemctl", "is-active", SERVICE, check=False).stdout.strip()

    def account_snapshot(self, name: str) -> str:
        return root("sh", "-c", 'getent passwd "$0"; getent shadow "$0"; id "$0"; '
                    'getent group | grep -E "(:|,)$0(,|$)|^$0:" || true; ls -lnd "/home/$0"',
                    name).stdout

    def path_exists(self, path: str) -> bool:
        return root("test", "-e", path, check=False).returncode == 0

    def install(self, bundle_dir: str, *flags: str, timeout: float = 300,
                cwd: Optional[str] = None) -> subprocess.CompletedProcess:
        return dexec("sudo", "-n", "bash", bundle_dir + "/install.sh", *flags, workdir=cwd or bundle_dir,
                     timeout=timeout, check=False)

    def plant(self) -> str:
        """A directory whose panellab_agent/ctl.py and json.py would print PLANTED if imported."""
        d = "/home/admin/planted"
        dexec("python3", "-I", "-c", (
            "import os, sys\n"
            "d = sys.argv[1]\n"
            "os.makedirs(os.path.join(d, 'panellab_agent'), exist_ok=True)\n"
            "open(os.path.join(d, 'panellab_agent', '__init__.py'), 'w').close()\n"
            "for f in ('panellab_agent/ctl.py', 'json.py'):\n"
            "    with open(os.path.join(d, f), 'w') as fh:\n"
            "        fh.write('print(\"PLANTED\")\\nraise SystemExit(0)\\n')\n"), d)
        return d

    # --------------------------------------------------------------- bundle
    def copy_bundle(self, version_override: Optional[str] = None) -> str:
        init = (AGENT / "panellab_agent" / "__init__.py").read_text(encoding="utf-8")
        version = re.search(r'^__version__ = "([^"]+)"', init, re.M).group(1)
        if version_override:
            init = init.replace('__version__ = "%s"' % version, '__version__ = "%s"' % version_override)
            version = version_override
        top = "panellab-agent-%s" % version
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            def add(path: Path, arcname: str, data: Optional[bytes] = None, mode: int = 0o644):
                payload = path.read_bytes() if data is None else data
                info = tarfile.TarInfo("%s/%s" % (top, arcname))
                info.size = len(payload)
                info.mode = mode
                info.mtime = int(time.time())
                tar.addfile(info, io.BytesIO(payload))
            add(AGENT / "install.sh", "install.sh", mode=0o755)
            add(AGENT / "systemd" / "panellab-agent.service", "systemd/panellab-agent.service")
            for py in sorted((AGENT / "panellab_agent").glob("*.py")):
                data = init.encode("utf-8") if py.name == "__init__.py" else None
                add(py, "panellab_agent/" + py.name, data=data)
        subprocess.run(["docker", "cp", "-", "%s:/home/admin" % CONTAINER], input=buf.getvalue(),
                       capture_output=True, timeout=60, check=True)
        dexec("chown", "-R", "admin:admin", "/home/admin/" + top, user="root")
        return "/home/admin/" + top

    # ---------------------------------------------------------------- steps
    def start_fixture(self, publish_ssh: bool = False) -> None:
        print("building %s ..." % IMAGE, flush=True)
        run(["docker", "build", "-q", "-t", IMAGE, str(FIXTURE)], timeout=900)
        remove_container()
        publish = ["--publish", "127.0.0.1::22"] if publish_ssh else []
        run(["docker", "run", "-d", "--name", CONTAINER, "--hostname", HOSTNAME,
             "--label", "panellab.purpose=agent-lab", *publish,
             "--privileged", "--cgroupns=host", "-v", "/sys/fs/cgroup:/sys/fs/cgroup:rw",
             "--tmpfs", "/run", "--tmpfs", "/run/lock", IMAGE])

        def booted():
            r = dexec("systemctl", "is-system-running", user="root", check=False)
            state = r.stdout.strip()
            if state in ("running", "degraded"):
                return state
            if state in ("starting", "initializing", ""):
                return None
            raise LabError("system state %r" % state)
        state = wait_for("systemd to boot", booted, timeout=90)
        self.check("systemd is PID 1 and booted (%s)" % state, True)
        self.check("mail sink active", self.is_active_unit("mail-sink.service") == "active")

    def is_active_unit(self, unit: str) -> str:
        return root("systemctl", "is-active", unit, check=False).stdout.strip()

    def verify_install(self, bundle: str, agent_before: str) -> None:
        r = self.install(bundle)
        self.check("install.sh exits 0", r.returncode == 0, r.stderr[-1500:] + r.stdout[-800:])
        self.check("install.sh prints the re-login note", RELOGIN_NOTE in r.stdout, r.stdout[-500:])
        self.check("service is active", self.is_active() == "active")
        self.check("service is enabled",
                   root("systemctl", "is-enabled", SERVICE, check=False).stdout.strip() == "enabled")
        sock = root("stat", "-c", "%U:%G %a", "/run/panellab-agent/agent.sock", check=False).stdout.strip()
        self.check("socket owned by panellab:panellab mode 0660", sock == "panellab:panellab 660", sock)
        rdir = root("stat", "-c", "%U:%G %a", "/run/panellab-agent").stdout.strip()
        self.check("runtime dir panellab:panellab 0750", rdir == "panellab:panellab 750", rdir)
        id_admin = dexec("id", "admin").stdout
        self.check("id admin includes panellab", "(panellab)" in id_admin, id_admin.strip())
        id_pl = dexec("id", "panellab").stdout
        self.check("id panellab includes adm", "(adm)" in id_pl, id_pl.strip())
        self.check("panellab is not in the sudo group", "(sudo)" not in id_pl, id_pl.strip())
        sudo_l = root("sudo", "-n", "-l", "-U", "panellab", check=False)
        self.check("panellab has no sudoers entry", "not allowed to run sudo" in sudo_l.stdout,
                   (sudo_l.stdout + sudo_l.stderr).strip())
        shell = dexec("getent", "passwd", "panellab").stdout.strip().split(":")[-1]
        self.check("panellab has no login shell", shell in ("/usr/sbin/nologin", "/sbin/nologin", "/bin/false"),
                   shell)
        self.check("existing agent account unchanged", self.account_snapshot("agent") == agent_before)

        props = self.unit("User", "Group", "Restart", "RestartUSec", "NoNewPrivileges", "ProtectSystem",
                          "PrivateTmp", "KillMode", "TimeoutStopUSec", "UMask", "ReadWritePaths",
                          "RuntimeDirectoryMode", "Environment")
        want = {"User": "panellab", "Group": "panellab", "Restart": "always", "RestartUSec": "5s",
                "NoNewPrivileges": "yes", "ProtectSystem": "full", "PrivateTmp": "yes",
                "KillMode": "control-group", "TimeoutStopUSec": "infinity", "UMask": "0007",
                "ReadWritePaths": "/etc/panellab-agent", "RuntimeDirectoryMode": "0750"}
        bad = {k: props.get(k) for k, v in want.items() if props.get(k) != v}
        self.check("unit hardening and restart properties", not bad, json.dumps(bad))
        self.check("unit sets PYTHONPATH", "PYTHONPATH=/opt/panellab-agent/current" in props.get("Environment", ""))
        user = dexec("ps", "-o", "user=", "-p", str(self.main_pid())).stdout.strip()
        self.check("daemon runs as panellab", user == "panellab", user)

        perms = root("stat", "-c", "%n %U:%G %a", "/var/lib/panellab-agent", "/etc/panellab-agent",
                     "/opt/panellab-agent", "/usr/local/bin/panellab-agentctl").stdout.split("\n")
        want_perms = ["/var/lib/panellab-agent panellab:panellab 750", "/etc/panellab-agent panellab:panellab 750",
                      "/opt/panellab-agent root:root 755", "/usr/local/bin/panellab-agentctl root:root 755"]
        self.check("directory owners and modes", [p for p in perms if p] == want_perms, json.dumps(perms))
        link = dexec("readlink", "/opt/panellab-agent/current").stdout.strip()
        self.check("current -> 0.1.0", link == "0.1.0", link)
        writable = dexec("sh", "-c", "test -w /opt/panellab-agent/current/panellab_agent && echo yes || echo no",
                         user="panellab", check=False).stdout.strip()
        self.check("program files not writable by panellab", writable == "no", writable)

        denied = dexec("panellab-agentctl", user="agent", input='{"protocol_version":1,"op":"status"}\n',
                       check=False)
        self.check("ctl as a non-member exits 4 (permission)", denied.returncode == 4,
                   "rc=%d %s" % (denied.returncode, denied.stderr.strip()))

        planted = self.plant()
        r = dexec("sudo", "-n", "panellab-agentctl", workdir=planted,
                  input='{"protocol_version":1,"op":"status"}\n', check=False)
        self.check("sudo panellab-agentctl ignores code in the current directory",
                   "PLANTED" not in r.stdout + r.stderr and '"ok": true' in r.stdout,
                   (r.stdout + r.stderr)[-400:])

        r2 = self.install(bundle)
        self.check("second install with same version is a restart",
                   r2.returncode == 0 and "只重启服务" in r2.stdout and self.is_active() == "active",
                   r2.stdout[-600:] + r2.stderr[-600:])

    def verify_protocol_and_alerts(self) -> None:
        status = self.ctl("status")
        self.check("status reports version 0.1.0 on %s" % HOSTNAME,
                   status["version"] == "0.1.0" and status["hostname"] == HOSTNAME, json.dumps(status)[:300])
        self.ctl("alerts.configure", {"smtp": {"host": "127.0.0.1", "port": 2525, "security": "none",
                                               "username": None, "from": "agent@lab.test",
                                               "to": ["admin@lab.test"]}})
        cfg = root("stat", "-c", "%U:%G %a", "/etc/panellab-agent/agent.json", check=False).stdout.strip()
        self.check("daemon wrote its own agent.json under ProtectSystem=full", cfg.startswith("panellab:panellab"),
                   cfg)
        before = len(self.mails())
        test = self.ctl("alerts.test_email")
        self.check("alerts.test_email ok", test.get("ok") is True, json.dumps(test))
        self.check("sink received the test mail", len(self.mails()) > before)

        job = {"id": "lab-echo", "name": "Lab echo", "workdir": "/var/lib/panellab-agent",
               "command": ["/bin/sh", "-c", "echo hello-from-lab; id -un"]}
        created = self.ctl("jobs.create", {"job": job})
        rid = self.ctl("jobs.run_now", {"id": "lab-echo"})["run_id"]
        run1 = self.wait_run_done("lab-echo", rid)
        out = self.ctl("runs.output", {"run_id": rid, "offset": 0, "limit": 65536})
        self.check("run now succeeded", run1["status"] == "succeeded", json.dumps(run1))
        self.check("run output readable and job ran as panellab",
                   "hello-from-lab" in out["data"] and "panellab" in out["data"] and out["eof"], json.dumps(out))

        mark = len(self.mails())
        failing = self.ctl("jobs.update", {"id": "lab-echo", "revision": created["revision"], "changes": {
            "command": ["/bin/sh", "-c", "echo boom-from-lab; exit 3"]}})
        rid = self.ctl("jobs.run_now", {"id": "lab-echo"})["run_id"]
        run2 = self.wait_run_done("lab-echo", rid)
        self.check("failing run recorded as failed with exit 3",
                   run2["status"] == "failed" and run2["exit_code"] == 3, json.dumps(run2))
        mail = self.wait_mail(r"作业失败：Lab echo", mark)
        self.check("failure alert mail received", "boom-from-lab" in mail["body"] and HOSTNAME in mail["subject"],
                   mail["subject"])

        mark = len(self.mails())
        self.ctl("jobs.update", {"id": "lab-echo", "revision": failing["revision"], "changes": {
            "command": ["/bin/sh", "-c", "echo fine-again"]}})
        rid = self.ctl("jobs.run_now", {"id": "lab-echo"})["run_id"]
        run3 = self.wait_run_done("lab-echo", rid)
        self.check("next run succeeded", run3["status"] == "succeeded", json.dumps(run3))
        mail = self.wait_mail(r"已恢复：作业失败：Lab echo", mark)
        self.check("recovery mail received", bool(mail), mail["subject"])

    def verify_crash_restart(self) -> None:
        self.ctl("jobs.create", {"job": {"id": "lab-sleep", "name": "Lab sleep",
                                         "workdir": "/var/lib/panellab-agent",
                                         "command": ["/bin/sh", "-c", "echo sleeping; exec sleep 300"]}})
        rid = self.ctl("jobs.run_now", {"id": "lab-sleep"})["run_id"]
        wait_for("lab-sleep to be running", lambda: (self.run_by_id("lab-sleep", rid) or {}).get("status") == "running")
        wait_for("sleep child", lambda: dexec("pgrep", "-u", "panellab", "-x", "sleep", check=False).returncode == 0)
        mark = len(self.mails())
        old_pid = self.main_pid()
        root("kill", "-9", str(old_pid))

        def restarted():
            pid = self.main_pid()
            return pid if pid and pid != old_pid and self.is_active() == "active" else None
        new_pid = wait_for("systemd to restart the agent", restarted, timeout=30)
        self.check("systemd restarted the daemon after kill -9 (pid %d -> %d)" % (old_pid, new_pid), True)
        wait_for("control socket", lambda: self.ctl("status"), timeout=15)
        orphan = dexec("pgrep", "-u", "panellab", "-x", "sleep", check=False)
        self.check("job process from the killed daemon is gone (KillMode=control-group)",
                   orphan.returncode != 0, orphan.stdout.strip())
        run = self.run_by_id("lab-sleep", rid) or {}
        self.check("interrupted run marked agent_lost", run.get("status") == "agent_lost", json.dumps(run))
        mail = self.wait_mail(r"作业失败：Lab sleep", mark)
        self.check("agent_lost alert mail received", bool(mail), mail["subject"])

    def verify_upgrade_drains(self) -> None:
        self.ctl("jobs.create", {"job": {"id": "lab-slow", "name": "Lab slow",
                                         "workdir": "/var/lib/panellab-agent",
                                         "command": ["/bin/sh", "-c", "sleep 8; echo slow-done"]}})
        bundle = self.copy_bundle(UPGRADE_VERSION)
        rid = self.ctl("jobs.run_now", {"id": "lab-slow"})["run_id"]
        wait_for("lab-slow running", lambda: (self.run_by_id("lab-slow", rid) or {}).get("status") == "running")
        old_pid = self.main_pid()
        began = time.monotonic()
        r = self.install(bundle, cwd=self.plant())
        took = time.monotonic() - began
        self.check("upgrade install.sh exits 0", r.returncode == 0, r.stderr[-1500:] + r.stdout[-800:])
        self.check("upgrade waited for the running job (%.1fs)" % took,
                   "排空中：还有 1 个运行未结束" in r.stderr and "排空完成" in r.stdout and took >= 4,
                   (r.stdout + r.stderr)[-800:])
        self.check("upgrade printed what it waits for", "lab-slow" in r.stdout, r.stdout[-600:])
        self.check("upgrade run from a planted directory never imports it",
                   "PLANTED" not in r.stdout + r.stderr, (r.stdout + r.stderr)[-400:])
        run = self.run_by_id("lab-slow", rid) or {}
        out = self.ctl("runs.output", {"run_id": rid, "offset": 0, "limit": 4096})
        self.check("running job finished normally during upgrade",
                   run.get("status") == "succeeded" and "slow-done" in out["data"], json.dumps(run))
        start_epoch = int(root("sh", "-c", 'date -u -d "$(systemctl show -p ExecMainStartTimestamp --value '
                                + SERVICE + ')" +%s').stdout.strip())
        finished = run.get("finished_at") or ""
        fin_epoch = int(dexec("date", "-u", "-d", finished, "+%s").stdout.strip()) if finished else 1 << 40
        self.check("service restarted only after the job finished",
                   self.main_pid() != old_pid and start_epoch >= fin_epoch,
                   "restart=%s finished=%s" % (start_epoch, finished))
        status = self.ctl("status")
        self.check("upgraded to %s and not draining" % UPGRADE_VERSION,
                   status["version"] == UPGRADE_VERSION and status["draining"] is False, json.dumps(status)[:300])
        link = dexec("readlink", "/opt/panellab-agent/current").stdout.strip()
        self.check("current -> %s" % UPGRADE_VERSION, link == UPGRADE_VERSION, link)
        jobs = sorted(j["id"] for j in self.ctl("jobs.list")["jobs"])
        self.check("jobs kept across upgrade", jobs == ["lab-echo", "lab-sleep", "lab-slow"], json.dumps(jobs))
        self.bundle = bundle

    def verify_uninstall(self, agent_before: str) -> None:
        r = self.install(self.bundle, "--uninstall")
        self.check("--uninstall exits 0", r.returncode == 0, r.stderr[-800:] + r.stdout[-800:])
        self.check("service gone after uninstall", self.is_active() != "active"
                   and not self.path_exists("/etc/systemd/system/panellab-agent.service"))
        self.check("program and ctl removed", not self.path_exists("/opt/panellab-agent")
                   and not self.path_exists("/usr/local/bin/panellab-agentctl"))
        self.check("data and config kept", self.path_exists("/var/lib/panellab-agent/agent.db")
                   and self.path_exists("/etc/panellab-agent/agent.json"))
        r = self.install(self.bundle, "--uninstall", "--purge")
        self.check("--uninstall --purge exits 0", r.returncode == 0, r.stderr[-800:] + r.stdout[-800:])
        self.check("data and config purged", not self.path_exists("/var/lib/panellab-agent")
                   and not self.path_exists("/etc/panellab-agent"))
        r = self.install(self.bundle, "--uninstall")
        self.check("uninstall again is harmless", r.returncode == 0, r.stderr[-800:])
        self.check("existing agent account still unchanged", self.account_snapshot("agent") == agent_before)


# ------------------------------------------------------------------ --panel

def loopback_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class PanelLab(Lab):
    """The same fixture, driven only through PanelLab's backend API (plus the sudo command)."""

    JOB = "lab-panel"
    JOB_NAME = "Lab panel"

    def __init__(self, workdir: Path) -> None:
        super().__init__()
        self.workdir = workdir
        self.backend: Optional[subprocess.Popen] = None
        self.client: Any = None
        self.smtp_secret = "smtp-secret-" + secrets.token_hex(8)
        self.job_secret = "job-secret-" + secrets.token_hex(8)
        self.admin_password = secrets.token_hex(16)
        self.db_password = secrets.token_hex(16)

    # ------------------------------------------------------------ backend
    def start_mysql(self) -> int:
        subprocess.run(["docker", "rm", "-f", MYSQL_CONTAINER], capture_output=True, timeout=60)
        run(["docker", "run", "-d", "--name", MYSQL_CONTAINER, "--label", "panellab.purpose=agent-lab",
             "--publish", "127.0.0.1::3306", "--tmpfs", "/var/lib/mysql",
             "--env", "MYSQL_ROOT_PASSWORD=" + self.db_password, "--env", "MYSQL_DATABASE=agent_lab",
             "--env", "MYSQL_USER=agent_lab", "--env", "MYSQL_PASSWORD=" + self.db_password, MYSQL_IMAGE])
        port = int(run(["docker", "port", MYSQL_CONTAINER, "3306/tcp"]).stdout.strip().splitlines()[0]
                   .rsplit(":", 1)[1])
        import pymysql

        def ready():
            try:
                pymysql.connect(host="127.0.0.1", port=port, user="agent_lab", password=self.db_password,
                                database="agent_lab", connect_timeout=2).close()
                return True
            except pymysql.MySQLError:
                return False
        wait_for("disposable MySQL", ready, timeout=120, interval=0.5)
        return port

    def start_backend(self) -> None:
        import httpx
        mysql_port = self.start_mysql()
        port = loopback_port()
        env = {**os.environ, "APP_ENV": "test", "ENABLE_DEMO_AUTH": "false",
               "SECRET_KEY": "agent-panel-lab-" + secrets.token_hex(24),
               "INIT_ADMIN_PASSWORD": self.admin_password, "NGINX_SKIP_APPLY": "true",
               "DATABASE_URL": "mysql+pymysql://agent_lab:%s@127.0.0.1:%d/agent_lab"
                               % (self.db_password, mysql_port)}
        subprocess.run([sys.executable, "-m", "init_db"], cwd=ROOT / "backend", env=env,
                       capture_output=True, check=True, timeout=60)
        self.log_path = self.workdir / "backend.log"
        log = self.log_path.open("wb")
        self.backend = subprocess.Popen([sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1",
                                         "--port", str(port)], cwd=ROOT / "backend", env=env,
                                        stdout=log, stderr=log)
        log.close()
        self.client = httpx.Client(base_url="http://127.0.0.1:%d" % port, timeout=180, trust_env=False)

        def healthy():
            if self.backend.poll() is not None:
                raise LabError("isolated backend exited; see %s" % self.log_path)
            try:
                return self.client.get("/api/health").status_code == 200
            except httpx.HTTPError:
                return False
        wait_for("isolated backend", healthy, timeout=60, interval=0.2)
        self.check("isolated backend up on 127.0.0.1:%d with a disposable MySQL" % port, True)

    def stop_backend(self) -> None:
        if self.client is not None:
            self.client.close()
        if self.backend is not None and self.backend.poll() is None:
            self.backend.terminate()
            try:
                self.backend.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.backend.kill()
                self.backend.wait(timeout=5)

    def api(self, method: str, path: str, status: int = 200, **kwargs: Any) -> Any:
        resp = self.client.request(method, path, **kwargs)
        if resp.status_code != status:
            raise LabError("%s %s -> %d (want %d): %s" % (method, path, resp.status_code, status,
                                                         resp.text[:500]))
        self.last_text = resp.text
        return resp.json() if resp.content else None

    def agent(self, method: str, path: str = "", status: int = 200, **kwargs: Any) -> Any:
        return self.api(method, "/api/servers/%d/agent%s" % (self.sid, path), status, **kwargs)

    def no_secrets(self, text: str) -> bool:
        return self.smtp_secret not in text and self.job_secret not in text

    # -------------------------------------------------------------- steps
    def connect_server(self) -> None:
        token = self.api("POST", "/api/auth/login",
                         json={"username": "admin", "password": self.admin_password})["token"]
        self.client.headers["Authorization"] = "Bearer " + token
        ssh_port = int(run(["docker", "port", CONTAINER, "22/tcp"]).stdout.strip().splitlines()[0]
                       .rsplit(":", 1)[1])
        server = self.api("POST", "/api/servers", json={
            "name": "Agent 测试夹具", "host": "127.0.0.1", "port": ssh_port,
            "username": "admin", "password": FIXTURE_PASSWORD})
        self.sid = server["id"]
        self.api("POST", "/api/servers/%d/connection/check" % self.sid, status=409)
        probe = self.api("POST", "/api/servers/%d/connection/probe" % self.sid, json={"auth_mode": "password"})
        keys = root("sh", "-c", 'for f in /etc/ssh/ssh_host_*_key.pub; do ssh-keygen -l -f "$f"; done').stdout
        fps = [line.split()[1] for line in keys.splitlines() if len(line.split()) > 1]
        self.check("probed fingerprint equals ssh-keygen -lf of a fixture host key",
                   probe["fingerprint"] in fps, "%s not in %s" % (probe["fingerprint"], fps))
        self.api("POST", "/api/servers/%d/connection/trust" % self.sid, json={"token": probe["token"]})
        self.api("POST", "/api/servers/%d/connection/check" % self.sid)
        self.check("host key pinned and SSH check passes (127.0.0.1:%d as admin)" % ssh_port, True)

    def install_through_panel(self) -> None:
        resp = self.client.get("/api/servers/%d/agent" % self.sid)
        code = (resp.json().get("detail") or {}).get("code") if resp.status_code != 200 else None
        self.check("status before install is agent_not_installed (404)",
                   resp.status_code == 404 and code == "agent_not_installed", resp.text[:300])
        result = self.agent("POST", "/install")
        command = result.get("command", "")
        m = re.fullmatch(r"sudo bash (/home/admin/panellab-agent-0\.1\.0-[0-9a-f]{12}/install\.sh)", command)
        self.check("install returns the one sudo command for an uploaded bundle",
                   bool(m) and result.get("version") == "0.1.0", json.dumps(result))
        if not m:
            raise LabError("unexpected install command")
        self.check("uploaded bundle is on the server", self.path_exists(m.group(1)))
        # Stand-in for the user typing the command: exactly that command, sudo made non-interactive.
        r = dexec("bash", "-c", "sudo -n " + command[len("sudo "):], workdir="/home/admin",
                  timeout=300, check=False)
        self.check("the returned command installs the agent", r.returncode == 0 and self.is_active() == "active",
                   r.stderr[-1500:] + r.stdout[-800:])
        status = self.agent("GET")
        self.check("panel status ok after install (new SSH session sees the group)",
                   status.get("version") == "0.1.0" and status.get("hostname") == HOSTNAME,
                   json.dumps(status)[:300])

    def jobs_and_alerts(self) -> None:
        cfg = self.agent("PUT", "/alerts", json={"smtp": {
            "host": "127.0.0.1", "port": 2525, "security": "none", "username": None,
            "password": self.smtp_secret, "from": "agent@lab.test", "to": ["admin@lab.test"]}})
        self.check("PUT /alerts stores SMTP and reports the password only as set",
                   cfg.get("smtp_password") == "已设置" and self.no_secrets(self.last_text), self.last_text[:300])

        job = {"id": self.JOB, "name": self.JOB_NAME, "workdir": "/var/lib/panellab-agent",
               "command": ["/bin/sh", "-c", 'echo panel-hello; id -un; '
                           'printf %s "$LAB_TOKEN" | sha256sum'],
               "schedule": {"cron": ["0 3 * * *"], "tz": "Asia/Shanghai"}}
        created = self.agent("POST", "/jobs", json={"job": job, "secret_env": {"LAB_TOKEN": self.job_secret}})
        self.check("POST /jobs creates the job without echoing secret_env",
                   created.get("id") == self.JOB and self.no_secrets(self.last_text), self.last_text[:300])
        got = self.agent("GET", "/jobs/" + self.JOB)
        self.check("GET /jobs/{id} lists the secret name as set, never its value",
                   got.get("secret_env") == [{"name": "LAB_TOKEN", "status": "已设置"}]
                   and self.no_secrets(self.last_text), self.last_text[:400])

        run1, out = self.run_through_panel()
        self.check("run now via panel succeeded", run1["status"] == "succeeded", json.dumps(run1))
        self.check("panel reads the output; job ran as panellab with its secret env",
                   "panel-hello" in out and "panellab" in out
                   and hashlib.sha256(self.job_secret.encode()).hexdigest() in out
                   and self.job_secret not in out, out[:300])

        mark = len(self.mails())
        rev = self.agent("PUT", "/jobs/" + self.JOB, json={"revision": got["revision"], "changes": {
            "command": ["/bin/sh", "-c", "echo panel-boom; exit 3"]}})["revision"]
        run2, _ = self.run_through_panel()
        self.check("induced failure recorded as failed exit 3",
                   run2["status"] == "failed" and run2.get("exit_code") == 3, json.dumps(run2))
        mail = self.wait_mail(r"作业失败：%s" % self.JOB_NAME, mark)
        self.check("failure mail landed in the fixture's SMTP sink",
                   "panel-boom" in mail["body"] and HOSTNAME in mail["subject"], mail["subject"])

        mark = len(self.mails())
        rev = self.agent("PUT", "/jobs/" + self.JOB, json={"revision": rev, "changes": {
            "command": ["/bin/sh", "-c", "echo panel-fine"]}})["revision"]
        run3, _ = self.run_through_panel()
        self.check("next run succeeded", run3["status"] == "succeeded", json.dumps(run3))
        mail = self.wait_mail(r"已恢复：作业失败：%s" % self.JOB_NAME, mark)
        self.check("recovery mail received", bool(mail), mail["subject"])

        self.agent("POST", "/jobs/%s/pause" % self.JOB)
        paused = self.agent("GET", "/jobs/" + self.JOB).get("paused")
        self.agent("POST", "/jobs/%s/resume" % self.JOB)
        resumed = self.agent("GET", "/jobs/" + self.JOB).get("paused")
        self.check("pause then resume via panel", paused is True and resumed is False,
                   "paused=%r resumed=%r" % (paused, resumed))

        cfg = self.agent("PUT", "/alerts", json={"thresholds": {"disk_percent": 85}})
        again = self.agent("GET", "/alerts")
        self.check("threshold change persisted and SMTP settings kept",
                   cfg.get("thresholds", {}).get("disk_percent") == 85
                   and again.get("thresholds", {}).get("disk_percent") == 85
                   and (again.get("smtp") or {}).get("host") == "127.0.0.1"
                   and again.get("smtp_password") == "已设置", json.dumps(again)[:400])
        self.check("GET /alerts never contains the SMTP password", self.no_secrets(self.last_text),
                   self.last_text[:300])

        mark = len(self.mails())
        test = self.agent("POST", "/alerts/test")
        self.check("POST /alerts/test ok", test.get("ok") is True, json.dumps(test))
        mail = self.wait_mail(r"测试邮件", mark)
        self.check("test mail received", HOSTNAME in mail["subject"], mail["subject"])

        self.agent("GET", "/jobs/" + self.JOB)
        job_text = self.last_text
        self.agent("GET", "/alerts")
        self.check("final GET /jobs/{id} and GET /alerts contain no secret value",
                   self.no_secrets(job_text) and self.no_secrets(self.last_text))
        self.client.close()
        log = self.log_path.read_text(encoding="utf-8", errors="replace")
        self.check("backend log contains no secret value", self.no_secrets(log))

    def print_log_tail(self) -> None:
        path = getattr(self, "log_path", None)
        if path is None or not path.exists():
            return
        text = path.read_text(encoding="utf-8", errors="replace")[-4000:]
        for value in (self.smtp_secret, self.job_secret, self.admin_password, self.db_password,
                      FIXTURE_PASSWORD):
            text = text.replace(value, "[redacted]")
        print("--- backend log tail ---\n%s\n--- end ---" % text)

    def run_through_panel(self) -> "tuple[Dict[str, Any], str]":
        rid = self.agent("POST", "/jobs/%s/run" % self.JOB)["run_id"]

        def finished():
            runs = self.agent("GET", "/jobs/%s/runs" % self.JOB)["runs"]
            r = next((x for x in runs if x["id"] == rid), None)
            return r if r and r["status"] != "running" else None
        run_row = wait_for("run %d via panel" % rid, finished, timeout=60, interval=1)
        out = self.agent("GET", "/runs/%d/output" % rid, params={"offset": 0, "limit": 65536})
        return run_row, out.get("data", "")


def main_panel(opts: argparse.Namespace) -> int:
    lab: Optional[PanelLab] = None
    with tempfile.TemporaryDirectory(prefix="panellab-agent-lab-") as tmp:
        lab = PanelLab(Path(tmp))
        try:
            lab.start_fixture(publish_ssh=True)
            lab.start_backend()
            lab.connect_server()
            lab.install_through_panel()
            lab.jobs_and_alerts()
        except (LabError, subprocess.SubprocessError, ValueError, KeyError, ImportError) as exc:
            lab.check("panel lab aborted", False, "%s: %s" % (type(exc).__name__, exc))
            lab.print_log_tail()
        except KeyboardInterrupt:
            lab.check("panel lab interrupted", False)
        finally:
            lab.stop_backend()
            subprocess.run(["docker", "rm", "-f", MYSQL_CONTAINER], capture_output=True, timeout=60)
            if opts.keep:
                print("container %s kept (remove with: docker rm -f %s)" % (CONTAINER, CONTAINER))
            else:
                remove_container()
    if lab.failures:
        print("agent panel lab: %d check(s) failed" % len(lab.failures))
        return 1
    print("agent panel lab: all checks passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--keep", action="store_true", help="leave the container running for debugging")
    parser.add_argument("--panel", action="store_true",
                        help="drive the fixture through an isolated PanelLab backend instead")
    opts = parser.parse_args()

    def on_term(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, on_term)
    if opts.panel:
        return main_panel(opts)

    lab = Lab()
    try:
        lab.start_fixture()
        agent_before = lab.account_snapshot("agent")
        bundle = lab.copy_bundle()
        lab.verify_install(bundle, agent_before)
        lab.verify_protocol_and_alerts()
        lab.verify_crash_restart()
        lab.verify_upgrade_drains()
        lab.verify_uninstall(agent_before)
    except (LabError, subprocess.SubprocessError, ValueError, KeyError) as exc:
        lab.check("lab aborted", False, "%s: %s" % (type(exc).__name__, exc))
    except KeyboardInterrupt:
        lab.check("lab interrupted", False)
    finally:
        if opts.keep:
            print("container %s kept (remove with: docker rm -f %s)" % (CONTAINER, CONTAINER))
        else:
            remove_container()
    if lab.failures:
        print("agent lab: %d check(s) failed" % len(lab.failures))
        return 1
    print("agent lab: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
