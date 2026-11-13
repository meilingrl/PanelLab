"""每日提醒邮件：当期简报生成后发一封 multipart/alternative（纯文本 + HTML）邮件。"""
import re
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from email.utils import format_datetime
from html import escape
from zoneinfo import ZoneInfo

SMTP_TIMEOUT = 60
SUMMARY_MAX = 80
EMPTY_TEXT = "今天没有新的要闻"


def _one_sentence(text):
    text = " ".join((text or "").split())
    m = re.search(r"[。！？!?]", text)
    if m:
        text = text[:m.end()]
    if len(text) > SUMMARY_MAX:
        text = text[:SUMMARY_MAX - 1] + "…"
    return text


def _flat(s):
    return " ".join(str(s).split())


def _top_events(store, issue, limit):
    rows = store.conn.execute(
        "select title_zh, summary_zh from events where issue_date = ? "
        "order by (section = 'top') desc, score desc, id limit ?", (issue, limit)).fetchall()
    return [(r[0], _one_sentence(r[1])) for r in rows]


def _compose(settings, issue, items, link, degraded=False):
    msg = EmailMessage()
    sender = _flat(settings.smtp.sender)
    msg["From"] = sender
    msg["To"] = ", ".join(_flat(r) for r in settings.smtp.recipients)
    first = items[0][0] if items else EMPTY_TEXT
    msg["Subject"] = _flat("每日简报 %s：%s%s" % (issue, first, "（部分降级）" if degraded else ""))
    if items:
        lines = ["%d. %s\n   %s" % (i, t, s) if s else "%d. %s" % (i, t)
                 for i, (t, s) in enumerate(items, 1)]
        text = "\n".join(lines)
        li = "".join("<li><strong>%s</strong>%s</li>" % (escape(t), "<br>" + escape(s) if s else "")
                     for t, s in items)
        body_html = "<ol>%s</ol>" % li
    else:
        text = EMPTY_TEXT
        body_html = "<p>%s</p>" % EMPTY_TEXT
    text = "每日简报 %s\n\n%s\n\n阅读全文：%s\n" % (issue, text, link)
    html = ('<html><body><h2>每日简报 %s</h2>%s<p><a href="%s">阅读全文</a></p></body></html>'
            % (issue, body_html, escape(link, quote=True)))
    msg.set_content(text, charset="utf-8")
    msg.add_alternative(html, subtype="html", charset="utf-8")
    return msg


def run_notify(settings, store, now: datetime, password: str) -> int:
    issue = now.astimezone(ZoneInfo(settings.timezone)).date().isoformat()
    row = store.conn.execute("select status from issues where date = ?", (issue,)).fetchone()
    if row is None or row[0] not in ("ok", "degraded"):
        print("%s 的简报尚未生成，不发提醒" % issue)
        return 2
    cfg = settings.smtp
    link = "%s/%s/" % (settings.site_url.rstrip("/"), issue)
    items = _top_events(store, issue, settings.notify_top)
    client = None
    try:
        msg = _compose(settings, issue, items, link, degraded=row[0] == "degraded")
        msg["Date"] = format_datetime(now)
        sender = _flat(cfg.sender)
        recipients = [_flat(r) for r in cfg.recipients]
        port = cfg.port or (465 if cfg.security == "ssl" else 587 if cfg.security == "starttls" else 25)
        if cfg.security == "ssl":
            client = smtplib.SMTP_SSL(cfg.host, port, timeout=SMTP_TIMEOUT,
                                      context=ssl.create_default_context())
        else:
            client = smtplib.SMTP(cfg.host, port, timeout=SMTP_TIMEOUT)
        client.ehlo()
        if cfg.security == "starttls":
            client.starttls(context=ssl.create_default_context())
            client.ehlo()
        if cfg.username:
            client.login(cfg.username, password)
        client.send_message(msg, from_addr=sender, to_addrs=recipients)
    except Exception as e:  # 只报异常类型：异常文字可能含服务器回复或密码
        print("提醒邮件发送失败：%s" % type(e).__name__)
        return 1
    finally:
        if client is not None:
            try:
                client.quit()
            except Exception:
                try:
                    client.close()
                except Exception:
                    pass
    print("已发送 %s 的提醒给 %d 位收件人" % (issue, len(recipients)))
    return 0
