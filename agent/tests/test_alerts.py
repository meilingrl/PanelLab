import base64
import email
import email.policy
import socket
import threading
from datetime import datetime, timezone

import pytest

from panellab_agent.alerts import AlertBook, MailError, Mailer
from panellab_agent.clock import FakeClock
from panellab_agent.store import Store

T0 = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


class FakeSMTPServer:
    """Tiny threaded stdlib SMTP responder that records messages."""

    def __init__(self, require_auth=False):
        self.messages = []
        self.auth = []
        self.require_auth = require_auth
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self._t = threading.Thread(target=self._serve, daemon=True)
        self._t.start()

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        f = conn.makefile("rwb")

        def say(line):
            f.write((line + "\r\n").encode())
            f.flush()

        try:
            say("220 fake ESMTP")
            authed = not self.require_auth
            while True:
                raw = f.readline()
                if not raw:
                    return
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                cmd = line.upper()
                if cmd.startswith("EHLO"):
                    f.write(b"250-fake\r\n250-AUTH PLAIN LOGIN\r\n250 8BITMIME\r\n")
                    f.flush()
                elif cmd.startswith("HELO"):
                    say("250 fake")
                elif cmd.startswith("AUTH PLAIN"):
                    parts = line.split()
                    blob = parts[2] if len(parts) > 2 else ""
                    _, user, pw = base64.b64decode(blob).decode().split("\0")
                    self.auth.append((user, pw))
                    authed = True
                    say("235 ok")
                elif cmd.startswith("AUTH LOGIN"):
                    say("334 VXNlcm5hbWU6")
                    user = base64.b64decode(f.readline().strip()).decode()
                    say("334 UGFzc3dvcmQ6")
                    pw = base64.b64decode(f.readline().strip()).decode()
                    self.auth.append((user, pw))
                    authed = True
                    say("235 ok")
                elif cmd.startswith("MAIL") or cmd.startswith("RCPT"):
                    say("250 ok" if authed else "530 auth required")
                elif cmd == "DATA":
                    say("354 go")
                    lines = []
                    while True:
                        l = f.readline()
                        if l in (b".\r\n", b""):
                            break
                        lines.append(l)
                    self.messages.append(b"".join(lines))
                    say("250 queued")
                elif cmd == "QUIT":
                    say("221 bye")
                    return
                else:
                    say("250 ok")
        finally:
            conn.close()

    def parsed(self):
        return [email.message_from_bytes(m, policy=email.policy.default) for m in self.messages]


def closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Env:
    def __init__(self, tmp_path, port, **smtp_over):
        self.clock = FakeClock(T0)
        self.store = Store(str(tmp_path / "a.db"), self.clock)
        smtp = {"host": "127.0.0.1", "port": port, "security": "none", "username": None,
                "from": "agent@example.com", "to": ["ops@example.com"]}
        smtp.update(smtp_over)
        self.config = {"smtp": smtp}
        self.password = None
        self.mailer = Mailer(lambda: self.config, lambda: self.password, self.clock)
        self.book = AlertBook(self.store, self.clock, self.mailer, "box1")


@pytest.fixture
def server():
    s = FakeSMTPServer()
    yield s
    s.close()


def test_fires_once_and_recovers_once(tmp_path, server):
    e = Env(tmp_path, server.port)
    e.book.observe("job:a", True, "任务失败", "退出码 1")
    e.book.observe("job:a", True, "任务失败", "退出码 1")
    assert len(server.messages) == 1
    assert e.store.get_alert("job:a")["state"] == "firing"
    e.clock.advance(600)
    e.book.observe("job:a", False, "任务失败", "")
    e.book.observe("job:a", False, "任务失败", "")
    msgs = server.parsed()
    assert len(msgs) == 2
    assert msgs[1]["Subject"] == "[PanelLab · box1] 已恢复：任务失败"
    assert e.store.get_alert("job:a")["state"] == "ok"
    assert e.mailer.last_result()["ok"] is True


def test_flapping_within_state_does_not_resend(tmp_path, server):
    e = Env(tmp_path, server.port)
    for _ in range(5):
        e.clock.advance(60)
        e.book.observe("k", False, "s", "b")
    assert server.messages == []
    for _ in range(5):
        e.clock.advance(60)
        e.book.observe("k", True, "s", "b")
    assert len(server.messages) == 1


def test_daily_reminder_after_24h(tmp_path, server):
    e = Env(tmp_path, server.port)
    e.book.observe("k", True, "磁盘将满", "b")
    e.clock.advance(24 * 3600 - 1)
    e.book.observe("k", True, "磁盘将满", "b")
    assert len(server.messages) == 1
    e.clock.advance(2)
    e.book.observe("k", True, "磁盘将满", "b")
    msgs = server.parsed()
    assert len(msgs) == 2
    assert msgs[1]["Subject"] == "[PanelLab · box1] 仍未恢复：磁盘将满"
    e.clock.advance(3600)
    e.book.observe("k", True, "磁盘将满", "b")
    assert len(server.messages) == 2


def test_smtp_unreachable_records_failure_without_raising(tmp_path):
    e = Env(tmp_path, closed_port())
    e.book.observe("k", True, "s", "b")
    res = e.mailer.last_result()
    assert res["ok"] is False and res["error"]
    assert e.store.get_alert("k")["state"] == "firing"
    with pytest.raises(MailError) as ei:
        e.mailer.send("s", "b")
    assert ei.value.code == "connect_failed"


def test_no_recipients_configured_skips_quietly_and_reports(tmp_path, server):
    e = Env(tmp_path, server.port, to=[])
    e.book.observe("k", True, "s", "b")
    e.book.notify("n", "b")
    assert server.messages == []
    assert e.store.get_alert("k")["state"] == "firing"
    assert e.mailer.last_result()["ok"] is False
    with pytest.raises(MailError) as ei:
        e.mailer.send("s", "b")
    assert ei.value.code == "not_configured"
    assert str(ei.value) == "尚未配置告警邮件" or ei.value.message == "尚未配置告警邮件"
    e.config = {}
    with pytest.raises(MailError):
        e.mailer.send("s", "b")


def test_subject_prefix_and_chinese_body_utf8(tmp_path, server):
    e = Env(tmp_path, server.port)
    e.book.observe("k", True, "备份失败", "磁盘已满：请清理")
    m = server.parsed()[0]
    assert m["Subject"] == "[PanelLab · box1] 备份失败"
    assert m["From"] == "agent@example.com" and m["To"] == "ops@example.com"
    assert m["Date"]
    body = m.get_content()
    assert "磁盘已满：请清理" in body and "box1" in body
    assert "2026-11-10T12:00:00+00:00" in body
    assert "utf-8" in m["Content-Type"].lower()


def test_notify_sends_one_off(tmp_path, server):
    e = Env(tmp_path, server.port)
    e.book.notify("Agent 已停止", "收到停止信号")
    e.book.notify("Agent 已停止", "收到停止信号")
    assert len(server.messages) == 2
    assert server.parsed()[0]["Subject"] == "[PanelLab · box1] Agent 已停止"


def test_notify_flags_skip_mail_but_advance_state(tmp_path, server):
    e = Env(tmp_path, server.port)
    e.book.observe("k", True, "s", "b", notify_firing=False)
    assert server.messages == []
    assert e.store.get_alert("k")["state"] == "firing"
    e.book.observe("k", False, "s", "b", notify_recovery=False)
    assert server.messages == []
    assert e.store.get_alert("k")["state"] == "ok"


def test_auth_login_with_username(tmp_path):
    s = FakeSMTPServer(require_auth=True)
    try:
        e = Env(tmp_path, s.port, username="u1")
        e.password = "pw-secret"
        e.mailer.send("s", "b")
        assert s.auth == [("u1", "pw-secret")]
        assert len(s.messages) == 1
    finally:
        s.close()


def test_auth_failure_hides_password(tmp_path):
    s = FakeSMTPServer(require_auth=True)
    try:
        e = Env(tmp_path, s.port)  # no username -> no login -> server rejects
        with pytest.raises(MailError) as ei:
            e.mailer.send("s", "b")
        assert ei.value.code in ("send_failed", "auth_failed")
        assert "pw" not in ei.value.message
    finally:
        s.close()


def test_failed_send_restarts_reminder_clock(tmp_path):
    e = Env(tmp_path, closed_port())
    e.book.observe("k", True, "s", "b")
    a = e.store.get_alert("k")
    assert a["since"] == T0.isoformat() and a["last_sent_at"] == T0.isoformat()


def test_crlf_in_subject_is_flattened(tmp_path, server):
    e = Env(tmp_path, server.port)
    e.book.observe("k", True, "a\r\nBcc: x@y.z", "b")
    m = server.parsed()[0]
    assert m["Subject"] == "[PanelLab · box1] a  Bcc: x@y.z"
    assert m["Bcc"] is None


def test_non_ascii_password_is_auth_failed_and_not_leaked(tmp_path):
    s = FakeSMTPServer(require_auth=True)
    try:
        e = Env(tmp_path, s.port, username="u1")
        e.password = "秘钥Zq9-secret"
        with pytest.raises(MailError) as ei:
            e.mailer.send("s", "b")
        exc = ei.value
        assert exc.code == "auth_failed"
        assert "Zq9" not in str(exc) and "Zq9" not in repr(exc)
        assert exc.__cause__ is None and exc.__context__ is None
        import traceback
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        assert "Zq9" not in tb
        assert "Zq9" not in repr(e.mailer.last_result())
        assert e.mailer.last_result()["ok"] is False
    finally:
        s.close()


def test_unknown_security_is_rejected(tmp_path, server):
    e = Env(tmp_path, server.port, security="tls")
    with pytest.raises(MailError) as ei:
        e.mailer.send("s", "b")
    assert ei.value.code == "not_configured" and ei.value.message == "告警邮件安全方式无效"
    assert server.messages == []
    assert e.mailer.last_result()["ok"] is False


def test_body_uses_quoted_printable(tmp_path, server):
    e = Env(tmp_path, server.port)
    e.mailer.send("s", "中文正文")
    m = server.parsed()[0]
    assert m["Content-Transfer-Encoding"] == "quoted-printable"
    assert m.get_content().strip() == "中文正文"


def test_bad_port_is_recorded_not_raised_raw(tmp_path, server):
    e = Env(tmp_path, server.port)
    e.config["smtp"]["port"] = "abc"
    with pytest.raises(MailError) as ei:
        e.mailer.send("s", "b")
    assert ei.value.code == "connect_failed"
    assert e.mailer.last_result()["ok"] is False


def test_invalid_config_shapes_are_mail_errors(tmp_path, server):
    e = Env(tmp_path, server.port)
    e.config = {"smtp": "oops"}
    with pytest.raises(MailError) as ei:
        e.mailer.send("s", "b")
    assert ei.value.code == "not_configured" and ei.value.message == "告警邮件配置无效"
    assert ei.value.__context__ is None
    assert e.mailer.last_result()["ok"] is False

    def boom():
        raise RuntimeError("x")

    m = Mailer(boom, lambda: None, e.clock)
    with pytest.raises(MailError) as ei:
        m.send("s", "b")
    assert ei.value.code == "not_configured" and ei.value.__context__ is None
    assert m.last_result()["error"] == "告警邮件配置无效"
    e.book.observe("k", True, "s", "b")  # still no exception via AlertBook
