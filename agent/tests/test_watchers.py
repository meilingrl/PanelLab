from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from panellab_agent.clock import FakeClock
from panellab_agent.watchers import (
    SSH_RECOVER_MINUTES, DiskWatch, Heartbeat, SshWatch, SustainedWatch,
    count_ssh_attempts, read_cpu_percent, read_mem_percent, ssh_recover_level,
)

T0 = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


def vfs(total, avail):
    return SimpleNamespace(f_blocks=total, f_bavail=avail)


def test_disk_hysteresis_90_85():
    state = {"used": 92}
    w = DiskWatch(lambda: "/dev/sda1 /data ext4 rw 0 0\n",
                  lambda p: vfs(100, 100 - state["used"]))
    r = w.check({})
    assert len(r) == 1
    key, firing, subject, body = r[0]
    assert key == "disk:/data" and firing is True
    assert "/data" in subject and "92%" in subject
    assert w.check({}) == []
    state["used"] = 87  # between 85 and 90: no change
    assert w.check({}) == []
    state["used"] = 84
    r = w.check({})
    assert [(x[0], x[1]) for x in r] == [("disk:/data", False)]
    state["used"] = 88  # below 90 so no re-fire
    assert w.check({}) == []


def test_disk_skips_pseudo_and_duplicates():
    mounts = (
        "tmpfs /run tmpfs rw 0 0\n"
        "proc /proc proc rw 0 0\n"
        "cgroup2 /sys/fs/cgroup cgroup2 rw 0 0\n"
        "/dev/sdb1 /mnt/b ext4 rw 0 0\n"
        "/dev/sda1 / ext4 rw 0 0\n"
        "/dev/sda1 /bind ext4 rw 0 0\n"
        "/dev/sdc1 /my\\040disk ext4 rw 0 0\n"
        "/dev/sdd1 /empty ext4 rw 0 0\n"
    )
    seen = []

    def sv(p):
        seen.append(p)
        return vfs(0, 0) if p == "/empty" else vfs(100, 1)

    w = DiskWatch(lambda: mounts, sv)
    keys = sorted(x[0] for x in w.check({}))
    assert keys == ["disk:/", "disk:/mnt/b", "disk:/my disk"]
    assert "/run" not in seen and "/bind" not in seen


def cpu_line(busy, idle):
    return "cpu  %d 0 0 %d 0 0 0 0 0 0" % (busy, idle)


def test_cpu_stat_and_mem_parsing():
    assert read_cpu_percent(cpu_line(0, 0), cpu_line(50, 50)) == 50.0
    assert read_cpu_percent(cpu_line(5, 5), cpu_line(5, 5)) is None
    assert read_cpu_percent("cpu  0 0 0 0 0 0 0 0", "cpu  10 0 0 0 10 0 0 0") == 50.0
    assert read_mem_percent("MemTotal: 1000 kB\nMemAvailable: 250 kB\n") == 75.0
    assert read_mem_percent("MemTotal: 1000 kB\n") is None


def test_cpu_needs_full_10_minutes():
    clock = FakeClock(T0)
    w = SustainedWatch("cpu", clock)
    results = []
    for _ in range(19):  # t=0..9:00 step 30
        results.append(w.sample(99, {}))
        clock.advance(30)
    assert results.count(True) == 0
    assert w.sample(99, {}) is None  # 9:30
    clock.advance(30)
    assert w.sample(99, {}) is True  # 10:00
    clock.advance(30)
    assert w.sample(99, {}) is None


def test_cpu_dip_resets_timer():
    clock = FakeClock(T0)
    w = SustainedWatch("cpu", clock)
    for _ in range(15):  # 7 min above
        assert w.sample(99, {}) is None
        clock.advance(30)
    assert w.sample(50, {}) is None  # dip
    clock.advance(30)
    for _ in range(20):  # 0..9:30 above, timer restarted at the dip
        assert w.sample(99, {}) is None
        clock.advance(30)
    assert w.sample(99, {}) is True  # 10:00


def test_cpu_recovers_after_5_minutes_below_90():
    clock = FakeClock(T0)
    w = SustainedWatch("cpu", clock)
    for _ in range(21):
        w.sample(99, {})
        clock.advance(30)
    out = []
    for _ in range(10):  # 0..4:30
        out.append(w.sample(80, {}))
        clock.advance(30)
    assert out == [None] * 10
    assert w.sample(80, {}) is False  # 5:00
    # staying low does not repeat
    clock.advance(30)
    assert w.sample(80, {}) is None


def test_sampling_gap_resets_timers():
    clock = FakeClock(T0)
    w = SustainedWatch("cpu", clock)
    w.sample(99, {})
    clock.advance(30 * 60)  # stalled for 30 min
    assert w.sample(99, {}) is None
    for _ in range(19):  # 0:30 .. 9:30 after the gap
        clock.advance(30)
        assert w.sample(99, {}) is None
    clock.advance(30)
    assert w.sample(99, {}) is True


def test_mem_uses_mem_threshold():
    clock = FakeClock(T0)
    w = SustainedWatch("mem", clock)
    cfg = {"mem_percent": 80, "sustain_minutes": 1}
    assert w.sample(85, cfg) is None
    clock.advance(60)
    assert w.sample(85, cfg) is True


def ssh_lines(n_a, n_b):
    lines = []
    for i in range(n_a):
        lines.append("2026-11-10T11:55:%02d+0000 h sshd[1]: Failed password for root from 1.2.3.4 port 22 ssh2" % (i % 60))
    for i in range(n_b):
        lines.append("2026-11-10T11:56:%02d+0000 h sshd[1]: Invalid user bob from 2001:db8::1 port 22" % (i % 60))
    return lines


def test_ssh_counts_last_10_minutes_top5():
    lines = ssh_lines(40, 17)
    for i in range(6):
        lines.append("x sshd[1]: authentication failure; logname= uid=0 rhost=9.9.9.%d user=root" % i)
    lines.append("x sshd[1]: Connection closed by authenticating user root 7.7.7.7 port 5 [preauth]")
    lines.append("unrelated line")
    seen = {}
    w = SshWatch(lambda since: seen.setdefault("since", since) and lines)
    firing, subject, body = w.check(T0, {})
    assert firing is True
    assert subject == "SSH 登录失败激增：10 分钟内 64 次"
    assert seen["since"] == T0 - __import__("datetime").timedelta(minutes=10)
    rows = body.splitlines()
    ip_rows = [r for r in rows if "  " in r and r.split()[-1].isdigit()]
    assert len(ip_rows) == 5
    assert ip_rows[0].split() == ["1.2.3.4", "40"]
    assert ip_rows[1].split() == ["2001:db8::1", "17"]


def test_ssh_below_threshold_not_firing():
    w = SshWatch(lambda since: ssh_lines(5, 0))
    assert w.check(T0, {})[0] is False
    w = SshWatch(lambda since: ssh_lines(30, 0))
    assert w.check(T0, {"ssh_failures": 31})[0] is False
    assert w.check(T0, {"ssh_failures": 30})[0] is True


def _invalid_user_attempt(pid, ip, user="admin"):
    """The three lines sshd logs for one password try by a non-existent user."""
    p = "2026-11-10T11:55:00+0000 h sshd[%d]: " % pid
    return [p + "Invalid user %s from %s port 4242" % (user, ip),
            p + "pam_unix(sshd:auth): check pass; user unknown",
            p + "pam_unix(sshd:auth): authentication failure; logname= uid=0 euid=0 "
                "tty=ssh ruser= rhost=%s" % ip,
            p + "Failed password for invalid user %s from %s port 4242 ssh2" % (user, ip)]


def test_ssh_counts_attempts_not_lines():
    lines = _invalid_user_attempt(100, "5.5.5.5") + _invalid_user_attempt(101, "5.5.5.5")
    # One connection trying three passwords for an existing user: 6 lines, 3 attempts.
    p = "2026-11-10T11:56:00+0000 h sshd-session[200]: "
    for _ in range(3):
        lines.append(p + "pam_unix(sshd:auth): authentication failure; logname= uid=0 "
                         "euid=0 tty=ssh ruser= rhost=6.6.6.6  user=root")
        lines.append(p + "Failed password for root from 6.6.6.6 port 1 ssh2")
    # Lines without a PID still count one each.
    lines.append("Nov 10 11:57:00 h sshd: Invalid user x from 7.7.7.7 port 2")
    lines.append("Nov 10 11:57:01 h sshd: Invalid user y from 7.7.7.7 port 3")
    counts = count_ssh_attempts(lines)
    assert counts == {"5.5.5.5": 2, "6.6.6.6": 3, "7.7.7.7": 2}
    w = SshWatch(lambda since: lines)
    firing, subject, _ = w.check(T0, {"ssh_failures": 7})
    assert firing is True and subject == "SSH 登录失败激增：10 分钟内 7 次"


def test_ssh_same_pid_different_ips_counted_separately():
    lines = _invalid_user_attempt(9, "1.1.1.1") + _invalid_user_attempt(9, "2.2.2.2")
    assert count_ssh_attempts(lines) == {"1.1.1.1": 1, "2.2.2.2": 1}


def _attempts(n):
    out = []
    for i in range(n):
        out += _invalid_user_attempt(1000 + i, "8.8.8.%d" % (i % 4))
    return out


def test_ssh_hysteresis_needs_sustained_quiet_to_recover():
    state = {"n": 0}
    w = SshWatch(lambda since: _attempts(state["n"]))
    cfg = {"ssh_failures": 20}  # recovery level: below 10
    t = T0

    def step(n, minutes=1):
        nonlocal t
        state["n"] = n
        t += timedelta(minutes=minutes)
        return w.check(t, cfg)[0]

    assert step(19) is False
    assert step(20) is True
    assert step(15) is True  # below trigger but not below the recovery level
    for _ in range(SSH_RECOVER_MINUTES - 1):
        assert step(9) is True
    assert step(25) is True  # a new burst restarts the quiet period
    for _ in range(SSH_RECOVER_MINUTES):
        assert step(3) is True
    assert step(3) is False  # 30 minutes below 10
    assert step(19) is False and step(20) is True


def test_ssh_recovery_body_mentions_rule():
    w = SshWatch(lambda since: _attempts(2))
    _, _, body = w.check(T0, {})
    assert "告警阈值 60 次" in body and "低于 30 次并持续 30 分钟后恢复" in body
    assert "8.8.8.0  1" in body


def test_ssh_gap_between_checks_restarts_recovery_timer():
    state = {"n": 30}
    w = SshWatch(lambda since: _attempts(state["n"]))
    cfg = {"ssh_failures": 30}
    assert w.check(T0, cfg)[0] is True
    state["n"] = 0
    assert w.check(T0 + timedelta(minutes=1), cfg)[0] is True
    # Agent paused for an hour: the old quiet start no longer counts.
    assert w.check(T0 + timedelta(minutes=61), cfg)[0] is True
    for m in range(62, 91):
        assert w.check(T0 + timedelta(minutes=m), cfg)[0] is True
    assert w.check(T0 + timedelta(minutes=91), cfg)[0] is False


def test_ssh_restored_firing_recovers_only_after_quiet_period():
    w = SshWatch(lambda since: [])
    w.firing = True
    for m in range(30):
        assert w.check(T0 + timedelta(minutes=m), {})[0] is True
    assert w.check(T0 + timedelta(minutes=30), {})[0] is False


def test_ssh_recover_level_is_half_trigger():
    assert ssh_recover_level(60) == 30
    assert ssh_recover_level(31) == 15
    assert ssh_recover_level(1) == 1


def test_ssh_disabled_clears_firing():
    w = SshWatch(lambda since: _attempts(60))
    assert w.check(T0, {})[0] is True
    assert w.check(T0, {"ssh_enabled": False})[0] is False
    assert w.firing is False


def test_ssh_disabled_does_not_read():
    def boom(since):
        raise AssertionError("must not read")
    assert SshWatch(boom).check(T0, {"ssh_enabled": False})[0] is False


class FakeResp:
    status = 200

    def close(self):
        pass


def test_heartbeat_disabled_without_url_and_rejects_http():
    calls = []
    clock = FakeClock(T0)
    for url in (None, "", "http://example.com/x"):
        h = Heartbeat(lambda u=url: u, clock,
                      opener=lambda *a, **k: calls.append(a) or FakeResp())
        h.tick()
        assert h.last_result() == {"at": None, "ok": None, "error": None}
    assert calls == []


def test_heartbeat_rate_limit_and_success():
    calls = []
    clock = FakeClock(T0)

    def opener(req, timeout=None):
        calls.append((req, timeout))
        return FakeResp()

    h = Heartbeat(lambda: "https://hc.example/ping/tok", clock, opener=opener)
    h.tick()
    clock.advance(299)
    h.tick()
    assert len(calls) == 1 and calls[0][1] == 10
    clock.advance(1)
    h.tick()
    assert len(calls) == 2
    r = h.last_result()
    assert r["ok"] is True and r["at"] and r["error"] is None


def test_heartbeat_failure_does_not_raise():
    clock = FakeClock(T0)

    def opener(req, timeout=None):
        raise OSError("boom https://hc.example/ping/secret-token")

    h = Heartbeat(lambda: "https://hc.example/ping/secret-token", clock, opener=opener)
    h.tick()
    r = h.last_result()
    assert r["ok"] is False and r["at"]
    assert "secret-token" not in str(r) and r["error"]


# ---- R21: effective recovery level = min(configured or default, trigger - 5)

def test_disk_lowered_trigger_fires_once_and_stays_firing():
    w = DiskWatch(lambda: "/dev/sda1 /data ext4 rw 0 0\n", lambda p: vfs(100, 18))  # 82%
    cfg = {"disk_percent": 80}
    r = w.check(cfg)
    assert [(x[0], x[1]) for x in r] == [("disk:/data", True)]
    assert "恢复阈值 75%" in r[0][3]
    for _ in range(5):
        assert w.check(cfg) == []


def test_disk_recover_uses_lower_of_configured_and_trigger_minus_5():
    state = {"used": 92}
    w = DiskWatch(lambda: "/dev/sda1 /data ext4 rw 0 0\n",
                  lambda p: vfs(100, 100 - state["used"]))
    cfg = {"disk_percent": 90, "disk_recover_percent": 70}
    assert w.check(cfg)[0][1] is True
    state["used"] = 80  # below 85 default but above configured 70
    assert w.check(cfg) == []
    state["used"] = 69
    assert w.check(cfg)[0][1] is False


def test_cpu_trigger_85_does_not_flap_at_85():
    clock = FakeClock(T0)
    w = SustainedWatch("cpu", clock)
    cfg = {"cpu_percent": 85, "sustain_minutes": 1, "recover_minutes": 1}
    out = []
    for _ in range(40):
        out.append(w.sample(85, cfg))
        clock.advance(30)
    assert out.count(True) == 1 and out.count(False) == 0
    assert w.firing is True
    # 81% is still above the effective recovery level 80
    for _ in range(10):
        assert w.sample(81, cfg) is None
        clock.advance(30)
    w.sample(79, cfg)
    clock.advance(60)
    assert w.sample(79, cfg) is False


# ---- I4: restored firing state and vanished mounts

def test_disk_restored_firing_recovers_when_low():
    state = {"used": 50}
    w = DiskWatch(lambda: "/dev/sda1 /data ext4 rw 0 0\n", lambda p: vfs(100, 100 - state["used"]))
    w.restore_firing("/data")
    r = w.check({})
    assert [(x[0], x[1]) for x in r] == [("disk:/data", False)]


def test_disk_vanished_mount_recovers():
    mounts = {"text": "/dev/sda1 /data ext4 rw 0 0\n/dev/sdb1 /backup ext4 rw 0 0\n"}
    w = DiskWatch(lambda: mounts["text"], lambda p: vfs(100, 1))
    assert sorted(x[0] for x in w.check({}) if x[1]) == ["disk:/backup", "disk:/data"]
    mounts["text"] = "/dev/sda1 /data ext4 rw 0 0\n"
    r = w.check({})
    assert [(x[0], x[1]) for x in r] == [("disk:/backup", False)]
    assert "/backup" in r[0][2]
    assert w.check({}) == []


def test_disk_empty_mount_list_keeps_firing_state():
    mounts = {"text": "/dev/sda1 /data ext4 rw 0 0\n"}
    w = DiskWatch(lambda: mounts["text"], lambda p: vfs(100, 1))
    w.check({})
    mounts["text"] = ""  # unreadable /proc/self/mounts, not an unmount
    assert w.check({}) == []
