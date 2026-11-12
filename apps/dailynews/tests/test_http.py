import pytest

from dailynews import __version__
from dailynews import http
from dailynews.http import FetchError


def test_get_returns_response(http_server):
    http_server.route("/a", body=b"hello", content_type="text/plain")
    r = http.get(http_server.url("/a"))
    assert r.status == 200
    assert r.body == b"hello"
    assert r.content_type == "text/plain"
    ua = http_server.requests[0].headers["User-Agent"]
    assert ua == "PanelLab-DailyNews/%s (+https://news.panellab.cn)" % __version__


def test_get_follows_http_redirect(http_server):
    http_server.route("/b", status=302, redirect="/a")
    http_server.route("/a", body=b"ok")
    r = http.get(http_server.url("/b"))
    assert r.body == b"ok"
    assert r.url == http_server.url("/a")


def test_get_rejects_non_http_redirect(http_server):
    http_server.route("/b", status=302, redirect="file:///etc/passwd")
    with pytest.raises(FetchError):
        http.get(http_server.url("/b"))


def test_get_rejects_non_http_url():
    with pytest.raises(FetchError):
        http.get("file:///etc/passwd")
    with pytest.raises(FetchError):
        http.get("ftp://example.com/x")


def test_get_limits_redirects(http_server):
    http_server.route("/loop", status=302, redirect="/loop")
    with pytest.raises(FetchError):
        http.get(http_server.url("/loop"))
    assert len(http_server.requests) == 6  # 初始请求 + 5 次重定向


def test_get_enforces_max_bytes(http_server):
    http_server.route("/big", body=b"x" * 2000)
    with pytest.raises(FetchError):
        http.get(http_server.url("/big"), max_bytes=1000)
    assert http.get(http_server.url("/big"), max_bytes=2000).status == 200


def test_get_error_status_is_returned_not_raised(http_server):
    http_server.route("/e", status=500, body=b"boom")
    r = http.get(http_server.url("/e"))
    assert r.status == 500


def test_get_timeout(http_server):
    http_server.route("/slow", body=b"x", delay=1.0)
    with pytest.raises(FetchError):
        http.get(http_server.url("/slow"), timeout=0.2)


def test_get_connection_refused():
    with pytest.raises(FetchError):
        http.get("http://127.0.0.1:1/", timeout=2)


def test_post_json(http_server):
    def handler(req):
        import json
        data = json.loads(req.body.decode("utf-8"))
        return 200, {"Content-Type": "application/json"}, json.dumps({"echo": data}).encode()
    http_server.route("/v1", method="POST", handler=handler)
    out = http.post_json(http_server.url("/v1"), {"a": 1}, {"Authorization": "Bearer t"}, 5)
    assert out == {"echo": {"a": 1}}
    req = http_server.requests[0]
    assert req.headers["Authorization"] == "Bearer t"
    assert req.headers["Content-Type"] == "application/json"


def test_post_json_errors(http_server):
    http_server.route("/err", method="POST", status=429, body=b"slow down")
    http_server.route("/bad", method="POST", body=b"not json")
    with pytest.raises(FetchError) as e:
        http.post_json(http_server.url("/err"), {}, {}, 5)
    assert "429" in str(e.value)
    with pytest.raises(FetchError):
        http.post_json(http_server.url("/bad"), {}, {}, 5)
