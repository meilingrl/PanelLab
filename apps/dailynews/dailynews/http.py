"""受限的 HTTP GET/POST：只允许 http/https，限制重定向次数与响应大小，固定 User-Agent。"""
import http.client
import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin, urlparse

from . import __version__

USER_AGENT = "PanelLab-DailyNews/%s (+https://news.panellab.cn)" % __version__
DEFAULT_TIMEOUT = 20.0
DEFAULT_MAX_BYTES = 5000000
MAX_REDIRECTS = 5
MIN_DOMAIN_INTERVAL = 1.0  # 同一域名两次请求之间至少间隔的秒数；测试里可改为 0
_REDIRECT_CODES = (301, 302, 303, 307, 308)


class FetchError(Exception):
    def __init__(self, message="", status=None):
        super().__init__(message)
        self.status = status  # HTTP 状态码；连接失败、超时等没有状态时为 None


@dataclass(frozen=True)
class Response:
    status: int
    url: str
    body: bytes
    content_type: str
    charset: Optional[str] = None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _check_url(url):
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise FetchError("只允许 http/https 地址：%s" % url)


def _read_limited(resp, max_bytes):
    length = resp.headers.get("Content-Length")
    if length and length.isdigit() and int(length) > max_bytes:
        raise FetchError("响应超过 %d 字节上限" % max_bytes)
    data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise FetchError("响应超过 %d 字节上限" % max_bytes)
    return data


def _open(req, timeout):
    """返回 (status, headers, body 读取函数)；HTTP 错误状态不抛异常。"""
    try:
        resp = _OPENER.open(req, timeout=timeout)
        return resp.status, resp
    except urllib.error.HTTPError as e:
        return e.code, e
    except urllib.error.URLError as e:
        raise FetchError("无法连接：%s" % (e.reason,))
    except (socket.timeout, TimeoutError):
        raise FetchError("请求超时")
    except (OSError, http.client.HTTPException, ValueError) as e:
        raise FetchError("请求失败：%s" % e)


def get(url, *, timeout=None, max_bytes=None):
    timeout = DEFAULT_TIMEOUT if timeout is None else timeout
    max_bytes = DEFAULT_MAX_BYTES if max_bytes is None else max_bytes
    current = url
    for hops in range(MAX_REDIRECTS + 1):
        _check_url(current)
        req = urllib.request.Request(current, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
        status, resp = _open(req, timeout)
        try:
            if status in _REDIRECT_CODES and resp.headers.get("Location"):
                if hops == MAX_REDIRECTS:
                    raise FetchError("重定向超过 %d 次" % MAX_REDIRECTS)
                current = urljoin(current, resp.headers["Location"])
                continue
            try:
                body = _read_limited(resp, max_bytes)
            except (socket.timeout, TimeoutError):
                raise FetchError("读取响应超时")
            except (OSError, http.client.HTTPException) as e:
                raise FetchError("读取响应失败：%s" % e)
            ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
            charset = resp.headers.get_content_charset()
            return Response(status, current, body, ctype, charset)
        finally:
            resp.close()
    raise FetchError("重定向超过 %d 次" % MAX_REDIRECTS)  # pragma: no cover


def post_json(url, payload, headers, timeout):
    """POST JSON 并返回解析后的 JSON 对象；非 2xx、非 JSON 或非对象都抛 FetchError。"""
    _check_url(url)
    h = {"User-Agent": USER_AGENT, "Content-Type": "application/json", "Accept": "application/json"}
    h.update(headers or {})
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    status, resp = _open(req, timeout)
    try:
        try:
            body = _read_limited(resp, DEFAULT_MAX_BYTES)
        except (socket.timeout, TimeoutError):
            raise FetchError("读取响应超时")
        except (OSError, http.client.HTTPException) as e:
            raise FetchError("读取响应失败：%s" % e)
    finally:
        resp.close()
    if not 200 <= status < 300:
        raise FetchError("HTTP %d：%s" % (status, body[:200].decode("utf-8", "replace")), status=status)
    try:
        out = json.loads(body.decode("utf-8"))
    except ValueError:
        raise FetchError("响应不是合法的 JSON")
    if not isinstance(out, dict):
        raise FetchError("响应不是 JSON 对象")
    return out


class DomainThrottle:
    """同一域名两次请求之间至少间隔 min_interval 秒（默认取 MIN_DOMAIN_INTERVAL）。"""

    def __init__(self, min_interval=None, sleep=time.sleep, clock=time.monotonic):
        self._min = min_interval
        self._sleep = sleep
        self._clock = clock
        self._last = {}

    def wait(self, url):
        interval = MIN_DOMAIN_INTERVAL if self._min is None else self._min
        host = (urlparse(url).hostname or "").lower()
        last = self._last.get(host)
        if last is not None and interval > 0:
            remaining = interval - (self._clock() - last)
            if remaining > 0:
                self._sleep(remaining)
        self._last[host] = self._clock()
