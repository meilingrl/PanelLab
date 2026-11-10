#!/usr/bin/env python3
"""Install the PanelLab Agent with install.sh into a disposable systemd container and check it.

Build (done automatically): docker build -t panellab-agent-lab:20261110 scripts/fixtures/agent_systemd
Run: python3 scripts/verify_agent_lab.py [--keep]

Everything runs through `docker exec` as the fixture's `admin` user, with `sudo -n`
where root is needed (admin has NOPASSWD sudo in this fixture only). Mail goes to the
fixture's SMTP sink on 127.0.0.1:2525, which stores each message in /var/mail-sink/.

Only the container named panellab-agent-lab is created and removed here (also on Ctrl-C);
no other containers, images, networks or volumes are touched. Never points at a real server.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import signal
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "agent"
FIXTURE = ROOT / "scripts" / "fixtures" / "agent_systemd"
IMAGE = "panellab-agent-lab:20261110"
CONTAINER = "panellab-agent-lab"
HOSTNAME = "agent-lab"
SERVICE = "panellab-agent.service"
RELOGIN_NOTE = "请重新登录 SSH 使组权限生效"
UPGRADE_VERSION = "0.1.1-lab"

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
    def start_fixture(self) -> None:
        print("building %s ..." % IMAGE, flush=True)
        run(["docker", "build", "-q", "-t", IMAGE, str(FIXTURE)], timeout=900)
        remove_container()
        run(["docker", "run", "-d", "--name", CONTAINER, "--hostname", HOSTNAME,
             "--label", "panellab.purpose=agent-lab",
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--keep", action="store_true", help="leave the container running for debugging")
    opts = parser.parse_args()

    def on_term(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, on_term)

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
