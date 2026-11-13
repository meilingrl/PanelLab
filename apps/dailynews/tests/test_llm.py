import json
from datetime import date

import pytest

from dailynews.llm import LLM, BudgetExceeded, LLMError
from dailynews.settings import LLMSettings
from dailynews.store import Store

DAY = date(2026, 11, 12)
KEY = "sk-secret-key-123"


def ok_body(content):
    return json.dumps({"choices": [{"message": {"content": content}}]}).encode()


@pytest.fixture
def store(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    yield s
    s.close()


def make(server, store, cap=2000, sleeps=None):
    st = LLMSettings(base_url=server.base_url + "/v1/", model="m1", max_calls_per_day=cap, timeout=5)
    log = sleeps if sleeps is not None else []
    return LLM(st, KEY, store, DAY, sleep=log.append)


def test_chat_json_sends_model_and_json_mode(http_server, store):
    http_server.route("/v1/chat/completions", method="POST",
                      handler=lambda r: (200, {}, ok_body('{"a": 1}')))
    out = make(http_server, store).chat_json("任务：cluster\nsys", "usr", max_tokens=123)
    assert out == {"a": 1}
    req = http_server.requests[0]
    body = json.loads(req.body)
    assert req.headers["Authorization"] == "Bearer " + KEY
    assert body["model"] == "m1"
    assert body["response_format"] == {"type": "json_object"}
    assert body["temperature"] == 0.2
    assert body["max_tokens"] == 123
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert body["messages"][1]["content"] == "usr"
    assert store.llm_calls_today(DAY) == 1


def test_retries_on_429_then_succeeds(http_server, store):
    seq = [(429, {}, b"slow down"), (503, {}, b"x"), (200, {}, ok_body("{}"))]
    http_server.route("/v1/chat/completions", method="POST", handler=lambda r: seq.pop(0))
    sleeps = []
    assert make(http_server, store, sleeps=sleeps).chat_json("s", "u") == {}
    assert sleeps == [2, 6]
    assert store.llm_calls_today(DAY) == 3


def test_gives_up_after_two_retries(http_server, store):
    http_server.route("/v1/chat/completions", method="POST", handler=lambda r: (500, {}, b"boom"))
    with pytest.raises(LLMError) as ei:
        make(http_server, store).chat_json("s", "u")
    assert "500" in str(ei.value)
    assert len(http_server.requests) == 3


def test_401_not_retried(http_server, store):
    http_server.route("/v1/chat/completions", method="POST", handler=lambda r: (401, {}, b"bad key sk-body"))
    sleeps = []
    with pytest.raises(LLMError) as ei:
        make(http_server, store, sleeps=sleeps).chat_json("s", "u")
    assert "401" in str(ei.value)
    assert len(http_server.requests) == 1 and sleeps == []


def test_garbled_200_envelope_retried_once_without_sleep(http_server, store):
    http_server.route("/v1/chat/completions", method="POST", handler=lambda r: (200, {}, b"<html>oops</html>"))
    sleeps = []
    with pytest.raises(LLMError):
        make(http_server, store, sleeps=sleeps).chat_json("s", "u")
    assert len(http_server.requests) == 2 and sleeps == []


def test_non_json_raises_after_one_retry(http_server, store):
    http_server.route("/v1/chat/completions", method="POST",
                      handler=lambda r: (200, {}, ok_body("not json at all")))
    with pytest.raises(LLMError):
        make(http_server, store).chat_json("s", "u")
    assert len(http_server.requests) == 2


def test_non_object_json_then_valid(http_server, store):
    seq = [ok_body("[1,2]"), ok_body('{"ok": true}')]
    http_server.route("/v1/chat/completions", method="POST", handler=lambda r: (200, {}, seq.pop(0)))
    assert make(http_server, store).chat_json("s", "u") == {"ok": True}


def test_budget_exceeded(http_server, store):
    http_server.route("/v1/chat/completions", method="POST", handler=lambda r: (200, {}, ok_body("{}")))
    llm = make(http_server, store, cap=2)
    llm.chat_json("s", "u")
    llm.chat_json("s", "u")
    with pytest.raises(BudgetExceeded):
        llm.chat_json("s", "u")
    assert len(http_server.requests) == 2
    assert issubclass(BudgetExceeded, LLMError)


def test_key_never_in_error_text(http_server, store):
    http_server.route("/v1/chat/completions", method="POST",
                      handler=lambda r: (401, {}, ("invalid " + KEY + " leaked body").encode()))
    with pytest.raises(LLMError) as ei:
        make(http_server, store).chat_json("s", "u")
    assert KEY not in str(ei.value) and "leaked body" not in str(ei.value)


def test_fake_llm_list_and_dispatch(fake_llm):
    from conftest import FakeLLM
    f = FakeLLM([{"a": 1}, {"b": 2}])
    assert f.chat_json("sys", "u1") == {"a": 1}
    assert f.chat_json("sys", "u2", max_tokens=5) == {"b": 2}
    assert [c[2] for c in f.calls] == ["u1", "u2"]
    g = FakeLLM({"classify": lambda s, u: {"u": u}, "link": lambda s, u: (_ for _ in ()).throw(LLMError("x"))})
    assert g.chat_json("任务：classify\nrest", "hi") == {"u": "hi"}
    assert g.calls[0][0] == "classify"
    with pytest.raises(LLMError):
        g.chat_json("任务：link\n", "hi")
    assert fake_llm.calls == []
