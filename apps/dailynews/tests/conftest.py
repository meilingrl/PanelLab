import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, List, Optional

import pytest

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fixture_bytes(name):
    with open(os.path.join(FIXTURES, name), "rb") as f:
        return f.read()


class RecordedRequest:
    def __init__(self, method, path, headers, body):
        self.method = method
        self.path = path
        self.headers = headers
        self.body = body


class LocalServer:
    """127.0.0.1 上的可配置 HTTP 服务器。

    server.route("/feed.xml", body=b"...", status=200, headers={}, delay=0, redirect=None)
    server.route("/v1/chat", method="POST", handler=lambda req: (200, {}, b"{}"))
    handler 接收 RecordedRequest，返回 (status, headers, body)。
    """

    def __init__(self):
        self.routes = {}  # type: Dict[tuple, dict]
        self.requests = []  # type: List[RecordedRequest]
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *args):
                pass

            def _serve(self, method):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                req = RecordedRequest(method, self.path, self.headers, body)
                outer.requests.append(req)
                path = self.path.split("?", 1)[0]
                spec = outer.routes.get((method, path))
                if spec is None:
                    status, headers, payload = 404, {}, b"not found"
                elif spec["handler"] is not None:
                    status, headers, payload = spec["handler"](req)
                elif spec["redirect"] is not None:
                    status, headers, payload = spec["status"], {"Location": spec["redirect"]}, b""
                else:
                    status, headers, payload = spec["status"], dict(spec["headers"]), spec["body"]
                if spec is not None and spec["delay"]:
                    time.sleep(spec["delay"])
                try:
                    self.send_response(status)
                    headers = dict(headers)
                    headers.setdefault("Content-Type", "application/octet-stream")
                    for k, v in headers.items():
                        self.send_header(k, v)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def do_GET(self):
                self._serve("GET")

            def do_POST(self):
                self._serve("POST")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.base_url = "http://127.0.0.1:%d" % self.port
        self._thread = threading.Thread(target=self.httpd.serve_forever,
                                        kwargs={"poll_interval": 0.01}, daemon=True)
        self._thread.start()

    def route(self, path, body=b"", status=200, headers=None, delay=0.0, redirect=None,
              method="GET", handler=None, content_type=None):
        h = dict(headers or {})
        if content_type:
            h["Content-Type"] = content_type
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.routes[(method, path)] = {"body": body, "status": status, "headers": h,
                                       "delay": delay, "redirect": redirect, "handler": handler}
        return self.base_url + path

    def url(self, path):
        return self.base_url + path

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self._thread.join(timeout=5)


@pytest.fixture
def http_server():
    server = LocalServer()
    yield server
    server.close()


DEFAULT_SETTINGS = os.path.join(ROOT, "default_settings.json")


def raw_settings(sources):
    """默认设置的副本，来源换成给定列表（字典，可省略 name/category/type/enabled）。"""
    import json
    with open(DEFAULT_SETTINGS, encoding="utf-8") as f:
        raw = json.load(f)
    full = []
    for s in sources:
        d = {"name": s["id"], "category": "world", "type": "media", "enabled": True}
        d.update(s)
        full.append(d)
    raw["sources"] = full
    return raw


def make_settings(sources):
    import json
    import tempfile
    from dailynews.settings import load_settings
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "settings.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(raw_settings(sources), f)
        return load_settings(p)


@pytest.fixture
def no_politeness(monkeypatch):
    """把同域名间隔降为 0，免得测试真的等待。"""
    from dailynews import http
    monkeypatch.setattr(http, "MIN_DOMAIN_INTERVAL", 0.0)


class FakeLLM:
    """测试用大模型：传 dict 列表则按调用顺序返回；传 {任务名: 函数(system, user) -> dict} 则按 system 首行“任务：<名>”分派。

    calls 记录 (任务名或 None, system, user)；函数可抛 LLMError 来模拟失败。
    """

    def __init__(self, responses=None):
        self._queue = None
        self._funcs = None
        if isinstance(responses, dict):
            self._funcs = responses
        else:
            self._queue = list(responses or [])
        self.calls = []

    @staticmethod
    def task_of(system):
        first = system.split("\n", 1)[0].strip()
        for prefix in ("任务：", "任务:"):
            if first.startswith(prefix):
                return first[len(prefix):].strip()
        return None

    def chat_json(self, system, user, *, max_tokens=2000):
        task = self.task_of(system)
        self.calls.append((task, system, user))
        if self._funcs is not None:
            fn = self._funcs.get(task)
            if fn is None:
                raise AssertionError("FakeLLM 没有为任务 %r 准备函数" % (task,))
            return fn(system, user)
        if not self._queue:
            raise AssertionError("FakeLLM 预设响应已用完")
        return self._queue.pop(0)


@pytest.fixture
def fake_llm():
    return FakeLLM([])
