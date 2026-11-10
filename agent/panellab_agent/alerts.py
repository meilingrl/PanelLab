"""Alert state machine (ok/firing per key) and SMTP mail sender."""
from __future__ import annotations

import smtplib
import ssl
import threading
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import format_datetime
from typing import Any, Callable, Dict, Optional

from .clock import Clock

SMTP_TIMEOUT = 20
REMINDER_INTERVAL = timedelta(hours=24)
NOT_CONFIGURED_MESSAGE = "尚未配置告警邮件"


class MailError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class Mailer:
    def __init__(self, config_getter: Callable[[], Dict[str, Any]],
                 password_getter: Callable[[], Optional[str]], clock: Clock) -> None:
        self._config = config_getter
        self._password = password_getter
        self._clock = clock
        self._last: Dict[str, Any] = {"at": None, "ok": False, "error": None}
        self._lock = threading.Lock()

    def last_result(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._last)

    def _record(self, ok: bool, error: Optional[str]) -> None:
        with self._lock:
            self._last = {"at": self._clock.now().isoformat(), "ok": ok, "error": error}

    def send(self, subject: str, body: str) -> None:
        try:
            self._send(subject, body)
        except MailError as exc:
            self._record(False, exc.message)
            raise
        self._record(True, None)

    def _send(self, subject: str, body: str) -> None:
        smtp_cfg = (self._config() or {}).get("smtp") or {}
        host = smtp_cfg.get("host")
        recipients = [r for r in (smtp_cfg.get("to") or []) if r]
        if not host or not recipients:
            raise MailError("not_configured", NOT_CONFIGURED_MESSAGE)
        security = smtp_cfg.get("security") or "starttls"
        port = int(smtp_cfg.get("port") or (465 if security == "ssl" else 25))
        sender = smtp_cfg.get("from") or smtp_cfg.get("username") or "panellab@localhost"
        username = smtp_cfg.get("username")

        msg = EmailMessage()
        msg["From"] = sender
        msg["To"] = ", ".join(recipients)
        msg["Subject"] = subject
        msg["Date"] = format_datetime(self._clock.now())
        msg.set_content(body, charset="utf-8")

        client = None
        try:
            try:
                if security == "ssl":
                    client = smtplib.SMTP_SSL(host, port, timeout=SMTP_TIMEOUT,
                                              context=ssl.create_default_context())
                else:
                    client = smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT)
            except (OSError, smtplib.SMTPException):
                raise MailError("connect_failed", "无法连接邮件服务器")
            try:
                client.ehlo()
                if security == "starttls":
                    client.starttls(context=ssl.create_default_context())
                    client.ehlo()
                if username:
                    client.login(username, self._password() or "")
                client.send_message(msg, from_addr=sender, to_addrs=recipients)
            except smtplib.SMTPAuthenticationError:
                raise MailError("auth_failed", "邮件服务器认证失败，请检查用户名和密码")
            except (OSError, smtplib.SMTPException, ssl.SSLError):
                raise MailError("send_failed", "邮件发送失败")
        finally:
            if client is not None:
                try:
                    client.quit()
                except Exception:
                    try:
                        client.close()
                    except Exception:
                        pass


class AlertBook:
    def __init__(self, store: Any, clock: Clock, mailer: Mailer,
                 config_getter: Callable[[], Dict[str, Any]], hostname: str) -> None:
        self._store = store
        self._clock = clock
        self._mailer = mailer
        self._config = config_getter
        self._hostname = hostname
        self._lock = threading.Lock()

    # ---- helpers ----------------------------------------------------------

    def _times(self, when: datetime) -> str:
        return "时间（UTC）：%s\n时间（服务器本地）：%s" % (
            when.isoformat(), when.astimezone().isoformat())

    def _compose(self, headline: str, when: datetime, body: str, extra: str = "") -> str:
        parts = [headline, "主机：%s" % self._hostname, self._times(when)]
        if extra:
            parts.append(extra)
        if body:
            parts.append("")
            parts.append(body)
        return "\n".join(parts) + "\n"

    def _send(self, subject: str, body: str) -> None:
        try:
            self._mailer.send("[PanelLab · %s] %s" % (self._hostname, subject), body)
        except MailError:
            pass  # recorded in mailer.last_result()
        except Exception as exc:  # never let mail trouble escape
            try:
                self._mailer._record(False, "邮件发送失败")
            except Exception:
                pass

    # ---- public -----------------------------------------------------------

    def notify(self, subject: str, body: str) -> None:
        now = self._clock.now()
        self._send(subject, self._compose("PanelLab 通知：%s" % subject, now, body))

    def observe(self, key: str, firing: bool, subject: str, body: str,
                notify_recovery: bool = True, notify_firing: bool = True) -> None:
        with self._lock:
            now = self._clock.now()
            now_s = now.isoformat()
            cur = self._store.get_alert(key)
            state = cur["state"] if cur else "ok"
            if firing and state != "firing":
                self._store.put_alert(key, "firing", now_s, now_s, subject)
                if notify_firing:
                    self._send(subject, self._compose("告警触发：%s" % subject, now, body))
            elif firing:
                last = cur.get("last_sent_at")
                last_dt = datetime.fromisoformat(last) if last else None
                if last_dt is None or now - last_dt >= REMINDER_INTERVAL:
                    self._store.put_alert(key, "firing", cur["since"], now_s, subject)
                    if notify_firing:
                        since = cur["since"]
                        self._send("仍未恢复：" + subject, self._compose(
                            "告警仍未恢复：%s" % subject, now, body,
                            "开始时间（UTC）：%s" % since))
            elif state == "firing":
                since = cur["since"]
                self._store.put_alert(key, "ok", now_s, None, subject)
                if notify_recovery:
                    orig = cur.get("message") or subject
                    self._send("已恢复：" + orig, self._compose(
                        "告警已恢复：%s" % orig, now, body,
                        "持续时间：自 %s（UTC）起，至 %s（UTC）恢复" % (since, now_s)))
