import email
import json
import smtplib
from datetime import datetime, timedelta, timezone
from email import policy

import pytest

from conftest import raw_settings
from dailynews import cli, notify
from dailynews.settings import load_settings
from dailynews.store import Store

TZ8 = timezone(timedelta(hours=8))
NOW = datetime(2026, 11, 13, 7, 30, tzinfo=TZ8)
ISSUE = "2026-11-13"


def _settings(tmp_path, port, **smtp):
    raw = raw_settings([{"id": "a", "url": "http://127.0.0.1:1/a.xml", "kind": "rss"}])
    raw["timezone"] = "Asia/Shanghai"
    raw["site_url"] = "https://news.example.com/"
    raw["data_dir"] = str(tmp_path / "data")
    raw["smtp"] = dict({"host": "127.0.0.1", "port": port, "security": "none", "username": "",
                        "sender": "news@example.com", "recipients": ["a@example.com", "b@example.com"]}, **smtp)
    p = tmp_path / "settings.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    return str(p), load_settings(str(p))


def _store(tmp_path, status="ok", events=()):
    st = Store(str(tmp_path / "n.db"))
    if status is not None:
        st.conn.execute("insert into issues(date, built_at, item_count, hidden_count, status) values (?,?,?,?,?)",
                        (ISSUE, "2026-11-12T23:20:00Z", len(events), 0, status))
    for title, summary, section, score in events:
        st.conn.execute("insert into events(issue_date, title_zh, summary_zh, section, score) values (?,?,?,?,?)",
                        (ISSUE, title, summary, section, score))
    st.conn.commit()
    return st


def _parse(raw):
    return email.message_from_bytes(raw, policy=policy.default)


def _bodies(msg):
    text = msg.get_body(preferencelist=("plain",)).get_content()
    html = msg.get_body(preferencelist=("html",)).get_content()
    return text, html


SEVEN = [("事件%d" % i, "第%d条摘要。后面还有一句。" % i, "top" if i < 3 else "category", 10 - i) for i in range(1, 8)]


def test_sends_top5_with_link(tmp_path, smtp_server):
    _, s = _settings(tmp_path, smtp_server.port)
    st = _store(tmp_path, events=list(reversed(SEVEN)))
    assert notify.run_notify(s, st, NOW, "") == 0
    assert len(smtp_server.messages) == 1
    frm, rcpts, raw = smtp_server.messages[0]
    assert frm == "news@example.com" and rcpts == ["a@example.com", "b@example.com"]
    msg = _parse(raw)
    assert msg["Subject"] == "每日简报 2026-11-13：事件1"
    assert msg.get_content_type() == "multipart/alternative"
    text, html = _bodies(msg)
    for body in (text, html):
        for i in range(1, 6):
            assert "事件%d" % i in body and "第%d条摘要。" % i in body
        assert "事件6" not in body and "事件7" not in body
        assert "后面还有一句" not in body
        assert "https://news.example.com/2026-11-13/" in body
    assert text.index("事件1") < text.index("事件2") < text.index("事件3")


@pytest.mark.parametrize("status", [None, "failed"])
def test_missing_issue_exit_2_no_mail(tmp_path, smtp_server, capsys, status):
    _, s = _settings(tmp_path, smtp_server.port)
    st = _store(tmp_path, status=status, events=SEVEN[:1])
    assert notify.run_notify(s, st, NOW, "") == 2
    assert "2026-11-13 的简报尚未生成，不发提醒" in capsys.readouterr().out
    assert smtp_server.messages == []


def test_html_escaped_in_mail(tmp_path, smtp_server):
    _, s = _settings(tmp_path, smtp_server.port)
    st = _store(tmp_path, events=[("<script>alert(1)</script> & 标题", "摘要 <b>粗</b>。", "top", 5)])
    assert notify.run_notify(s, st, NOW, "") == 0
    msg = _parse(smtp_server.messages[0][2])
    text, html = _bodies(msg)
    assert "<script>" not in html and "<b>" not in html
    assert "&lt;script&gt;" in html and "&amp;" in html
    assert "<script>alert(1)</script> & 标题" in text


def test_degraded_issue_still_notifies(tmp_path, smtp_server):
    _, s = _settings(tmp_path, smtp_server.port)
    st = _store(tmp_path, status="degraded", events=SEVEN[:1])
    assert notify.run_notify(s, st, NOW, "") == 0
    assert len(smtp_server.messages) == 1


def test_zero_events_still_sends(tmp_path, smtp_server):
    _, s = _settings(tmp_path, smtp_server.port)
    st = _store(tmp_path, events=[])
    assert notify.run_notify(s, st, NOW, "") == 0
    msg = _parse(smtp_server.messages[0][2])
    assert msg["Subject"] == "每日简报 2026-11-13：今天没有新的要闻"
    text, html = _bodies(msg)
    assert "今天没有新的要闻" in text and "https://news.example.com/2026-11-13/" in html


def test_degraded_issue_subject_marked(tmp_path, smtp_server):
    _, s = _settings(tmp_path, smtp_server.port)
    st = _store(tmp_path, status="degraded", events=SEVEN[:2])
    assert notify.run_notify(s, st, NOW, "") == 0
    msg = _parse(smtp_server.messages[0][2])
    assert msg["Subject"] == "每日简报 2026-11-13：事件1（部分降级）"


def test_issue_date_uses_settings_timezone(tmp_path, smtp_server):
    _, s = _settings(tmp_path, smtp_server.port)
    st = _store(tmp_path, events=SEVEN[:1])
    # UTC 2026-11-12 23:30 == 上海 2026-11-13 07:30
    assert notify.run_notify(s, st, datetime(2026, 11, 12, 23, 30, tzinfo=timezone.utc), "") == 0


def test_password_not_in_output_on_auth_error(tmp_path, smtp_server_factory, capsys):
    srv = smtp_server_factory(credentials=("user", "right"), auth_required=True)
    _, s = _settings(tmp_path, srv.port, username="user")
    st = _store(tmp_path, events=SEVEN[:1])
    assert notify.run_notify(s, st, NOW, "s3cret-wrong") == 1
    cap = capsys.readouterr()
    assert "s3cret-wrong" not in cap.out + cap.err
    assert "提醒邮件发送失败：SMTPAuthenticationError" in cap.out
    assert srv.auth_attempts and srv.messages == []


def test_login_when_username_given(tmp_path, smtp_server_factory):
    srv = smtp_server_factory(credentials=("user", "pw"), auth_required=True)
    _, s = _settings(tmp_path, srv.port, username="user")
    st = _store(tmp_path, events=SEVEN[:1])
    assert notify.run_notify(s, st, NOW, "pw") == 0
    assert len(srv.messages) == 1


def test_connection_failure_exit_1(tmp_path, smtp_server, capsys):
    _, s = _settings(tmp_path, 1)
    st = _store(tmp_path, events=SEVEN[:1])
    assert notify.run_notify(s, st, NOW, "") == 1
    assert "提醒邮件发送失败：" in capsys.readouterr().out


@pytest.mark.parametrize("security,cls,tls", [("ssl", "SMTP_SSL", False), ("starttls", "SMTP", True),
                                              ("none", "SMTP", False)])
def test_security_modes_pick_smtplib_branch(tmp_path, monkeypatch, security, cls, tls):
    used = []

    class Fake:
        def __init__(self, *a, **kw):
            used.append((type(self).__name__, a, kw))

        def ehlo(self):
            pass

        def starttls(self, **kw):
            used.append("starttls")

        def login(self, *a):
            pass

        def send_message(self, *a, **kw):
            used.append("send")

        def quit(self):
            pass

    class FakeSSL(Fake):
        pass

    monkeypatch.setattr(smtplib, "SMTP", Fake)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSSL)
    _, s = _settings(tmp_path, 465, security=security)
    st = _store(tmp_path, events=SEVEN[:1])
    assert notify.run_notify(s, st, NOW, "") == 0
    assert used[0][0] == ("FakeSSL" if cls == "SMTP_SSL" else "Fake")
    assert used[0][2]["timeout"] == 60
    assert ("starttls" in used) is tls and "send" in used


def test_cli_requires_smtp_config(tmp_path, capsys):
    p, s = _settings(tmp_path, 25)
    with open(p, encoding="utf-8") as f:
        raw = json.load(f)
    raw["smtp"] = {}
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps(raw))
    assert cli.main(["--settings", p, "notify", "--now", "2026-11-13T07:30:00+08:00"]) == 2
    assert "smtp" in capsys.readouterr().out.lower()


def test_cli_missing_password_when_auth_needed(tmp_path, monkeypatch, capsys):
    p, s = _settings(tmp_path, 25, username="user")
    monkeypatch.delenv("DAILYNEWS_SMTP_PASSWORD", raising=False)
    assert cli.main(["--settings", p, "notify", "--now", "2026-11-13T07:30:00+08:00"]) == 2
    assert "缺少 DAILYNEWS_SMTP_PASSWORD" in capsys.readouterr().err


def test_cli_notify_success_message(tmp_path, smtp_server, capsys):
    p, s = _settings(tmp_path, smtp_server.port)
    st = _store(tmp_path, events=SEVEN[:1])
    st.close()
    import os
    os.makedirs(s.data_dir, exist_ok=True)
    os.replace(str(tmp_path / "n.db"), os.path.join(s.data_dir, "dailynews.db"))
    assert cli.main(["--settings", p, "notify", "--now", "2026-11-13T07:30:00+08:00"]) == 0
    assert "已发送 2026-11-13 的提醒给 2 位收件人" in capsys.readouterr().out


@pytest.mark.parametrize("security,port", [("starttls", 587), ("ssl", 465), ("none", 25)])
def test_port_zero_defaults(tmp_path, monkeypatch, security, port):
    seen = []

    class Fake:
        def __init__(self, host, p, **kw):
            seen.append(p)

        def __getattr__(self, name):
            return lambda *a, **kw: None

    monkeypatch.setattr(smtplib, "SMTP", Fake)
    monkeypatch.setattr(smtplib, "SMTP_SSL", Fake)
    _, s = _settings(tmp_path, 0, security=security)
    st = _store(tmp_path, events=SEVEN[:1])
    assert notify.run_notify(s, st, NOW, "") == 0
    assert seen == [port]


def test_header_injection_blocked(tmp_path, smtp_server):
    _, s = _settings(tmp_path, smtp_server.port,
                     sender="news@example.com\r\nBcc: x@evil.example",
                     recipients=["a@example.com", "b@example.com\r\nBcc: y@evil.example"])
    st = _store(tmp_path, events=[("A\r\nBcc: x@evil.example", "摘要。", "top", 5)])
    notify.run_notify(s, st, NOW, "")
    assert smtp_server.messages
    for frm, rcpts, raw in smtp_server.messages:
        msg = _parse(raw)
        assert msg["Bcc"] is None
        for name in ("Subject", "From", "To"):
            assert "\n" not in str(msg[name]) and "\r" not in str(msg[name])
        head = raw.split(b"\r\n\r\n", 1)[0]
        assert b"\nBcc:" not in head and b"\r\nBcc:" not in head
        assert "x@evil.example" not in rcpts
        assert not any(r.startswith("\n") or "\n" in r or "\r" in r for r in rcpts)
