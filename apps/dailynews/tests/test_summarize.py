import re
from datetime import datetime, timezone

import pytest

from conftest import FakeLLM, make_settings
from dailynews.cluster import Linked
from dailynews.feeds import Item
from dailynews.llm import BudgetExceeded, LLMError
from dailynews.rank import Candidate, Selection
from dailynews.store import Store
from dailynews.summarize import EventOut, SourceLink, summarize

NOW = datetime(2026, 11, 12, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    s = Store(str(tmp_path / "d.sqlite"))
    yield s
    s.close()


@pytest.fixture
def settings():
    return make_settings([{"id": "bbc", "name": "BBC News", "url": "http://127.0.0.1/bbc", "kind": "rss"},
                          {"id": "gd", "name": "The Guardian", "url": "http://127.0.0.1/gd", "kind": "rss"}])


def _add(store, n, title, source="bbc", summary="", body=None, url=None):
    aid = store.add_articles([Item(source, url or "https://x.example/%s" % n, title, summary, None, "en")], NOW)[0]
    if body is not None:
        store.set_body(aid, body, "ok")
    return aid


def _cand(ids, category="world", status="new", prev=None, score=5.0):
    return Candidate(Linked(tuple(ids), status, prev), category, False, 3, score=score)


def _sel(top, by_category=None):
    return Selection(list(top), dict(by_category or {}), [], 0)


def _item(i, title="中文标题%d", summary="第一句。第二句。", detail="详情一。详情二。", change=None):
    d = {"i": i, "title_zh": title % i if "%d" in title else title, "summary_zh": summary, "detail_zh": detail}
    d["change_zh"] = change
    return d


def _numbers(user):
    return [int(n) for n in re.findall(r"^\[(\d+)\]", user, re.M)]


def _echo(change=None):
    """为 user 里的每个事件编号返回合格的一项。"""
    return lambda s, u: {"items": [_item(n, change=change) for n in _numbers(u)]}


def test_summary_fields_from_llm(store, settings):
    a = _add(store, 1, "Ceasefire agreed in Gaza", summary="Talks ended. More soon.",
             body="Negotiators agreed a ceasefire on Tuesday after long talks.")
    b = _add(store, 2, "Gaza truce deal reached", source="gd", summary="A truce was reached.")
    dup = _add(store, 3, "Gaza truce, more", source="gd", url="https://x.example/2#frag")
    c = _add(store, 4, "Chip export rules", summary="New rules.", url="ftp://x.example/4")
    llm = FakeLLM({"summarize": lambda s, u: {"items": [
        {"i": 1, "title_zh": "加沙达成停火", "summary_zh": "谈判方周二同意停火。消息来自多个来源。",
         "detail_zh": "长时间谈判后达成停火。", "change_zh": "无关字段"},
        {"i": 2, "title_zh": "芯片出口新规", "summary_zh": "出台新规。", "detail_zh": "", "change_zh": None},
    ]}})
    sel = _sel([_cand([a, b, dup], score=9.0)], {"tech": [_cand([c], category="tech", score=4.0)]})
    out = summarize(sel, store, llm, settings)

    assert len(llm.calls) == 1
    task, system, user = llm.calls[0]
    assert task == "summarize" and system.startswith("任务：summarize\n")
    for phrase in ("只依据", "不添加", "简体中文", "change_zh"):
        assert phrase in system
    assert "Negotiators agreed a ceasefire" in user          # 有正文时用正文
    assert "BBC News" in user and "The Guardian" in user and "Ceasefire agreed in Gaza" in user

    e = out[0]
    assert isinstance(e, EventOut)
    assert (e.title_zh, e.title_orig, e.category, e.status) == ("加沙达成停火", "Ceasefire agreed in Gaza", "world", "new")
    assert e.summary_zh == "谈判方周二同意停火。消息来自多个来源。"
    assert e.detail_zh == "长时间谈判后达成停火。"
    assert e.change_zh is None                 # 非 followup 忽略 change_zh
    assert e.sources == [SourceLink("BBC News", "https://x.example/1"), SourceLink("The Guardian", "https://x.example/2"),
                         SourceLink("The Guardian", "https://x.example/2#frag")]
    assert (e.degraded, e.basis, e.section, e.score) == (False, "body", "top", 9.0)
    assert e.article_ids == (a, b, dup) and e.prev_event_id is None and e.prev_issue is None

    t = out[1]
    assert (t.title_zh, t.section, t.category, t.score) == ("芯片出口新规", "category", "tech", 4.0)
    assert t.sources == []                      # 只允许 http/https
    assert t.basis == "snippet"


def test_output_order_top_then_settings_category_order(store, settings):
    ids = [_add(store, n, "Story %d" % n, summary="S%d." % n) for n in range(1, 6)]
    sel = _sel([_cand([ids[0]])],
               {"tech": [_cand([ids[3]], category="tech"), _cand([ids[4]], category="tech")],
                "politics": [_cand([ids[1]], category="politics")],
                "world": [_cand([ids[2]])]})
    out = summarize(sel, store, FakeLLM({"summarize": _echo()}), settings)
    assert [e.article_ids[0] for e in out] == [ids[0], ids[1], ids[2], ids[3], ids[4]]
    assert [e.section for e in out] == ["top", "category", "category", "category", "category"]


def test_followup_has_change_line(store, settings):
    store.conn.execute("insert into events(id, issue_date, title_zh) values (7, '2026-11-11', '旧事件')")
    store.conn.commit()
    a = _add(store, 1, "Truce extended", summary="The truce was extended by a week.")
    sel = _sel([_cand([a], status="followup", prev=7)])
    llm = FakeLLM({"summarize": _echo(change="停火延长一周。")})
    out = summarize(sel, store, llm, settings)
    e = out[0]
    assert (e.status, e.change_zh, e.prev_event_id, e.prev_issue, e.degraded) == (
        "followup", "停火延长一周。", 7, "2026-11-11", False)
    assert "followup" in llm.calls[0][2] or "后续" in llm.calls[0][2]
    assert store.event_issue_date(7) == "2026-11-11" and store.event_issue_date(99) is None

    # followup 缺少 change_zh → 该事件降级
    out2 = summarize(sel, store, FakeLLM({"summarize": _echo(change="")}), settings)
    assert out2[0].degraded and out2[0].change_zh is None and out2[0].prev_issue == "2026-11-11"


def test_snippet_basis_marked(store, settings):
    a = _add(store, 1, "Snippet only", summary="Only a summary here.")
    b = _add(store, 2, "Failed body", summary="Summary text.")
    store.set_body(b, "partial junk", "failed")
    c = _add(store, 3, "Has body", summary="x", body="Full body text.")
    llm = FakeLLM({"summarize": _echo()})
    out = summarize(_sel([_cand([a]), _cand([b]), _cand([b, c])]), store, llm, settings)
    assert [e.basis for e in out] == ["snippet", "snippet", "body"]
    assert all(not e.degraded for e in out)
    assert "partial junk" not in llm.calls[0][2]          # 失败的正文不作为依据


def test_prompt_caps_articles_and_text(store, settings):
    ids = [_add(store, n, "Title %d" % n, summary="s", body="B%d " % n + "x" * 5000) for n in range(1, 7)]
    llm = FakeLLM({"summarize": _echo()})
    out = summarize(_sel([_cand(ids)]), store, llm, settings)
    user = llm.calls[0][2]
    assert "Title 4" in user and "Title 5" not in user     # 每事件最多 4 篇
    assert "x" * 1500 not in user and "x" * 1400 in user    # 每篇约 1500 字
    assert len(out[0].sources) == 6                          # 来源链接仍列出全部文章


def test_budget_exceeded_degrades_remaining_only(store, settings):
    ids = [_add(store, n, "Story %d" % n, summary="First sentence %d. Second." % n) for n in range(1, 16)]
    calls = {"n": 0}

    def fn(s, u):
        calls["n"] += 1
        if calls["n"] == 2:
            raise BudgetExceeded("今日大模型调用已达上限")
        return _echo()(s, u)

    llm = FakeLLM({"summarize": fn})
    out = summarize(_sel([_cand([i]) for i in ids]), store, llm, settings)
    assert len(llm.calls) == 2                      # 第三批不再调用
    assert [len(_numbers(c[2])) for c in llm.calls] == [6, 6]
    assert all(not e.degraded for e in out[:6])
    assert out[0].title_zh == "中文标题1"
    rest = out[6:]
    assert len(rest) == 9 and all(e.degraded for e in rest)
    assert rest[0].title_zh == "Story 7" and rest[0].summary_zh == "First sentence 7."
    assert rest[0].detail_zh == "" and rest[0].change_zh is None and rest[0].basis == "snippet"


def test_llm_error_degrades_that_batch_and_continues(store, settings):
    ids = [_add(store, n, "Story %d" % n) for n in range(1, 8)]
    calls = {"n": 0}

    def fn(s, u):
        calls["n"] += 1
        if calls["n"] == 1:
            raise LLMError("bad")
        return _echo()(s, u)

    out = summarize(_sel([_cand([i]) for i in ids]), store, FakeLLM({"summarize": fn}), settings)
    assert calls["n"] == 2
    assert all(e.degraded for e in out[:6]) and not out[6].degraded
    assert out[0].summary_zh == "Story 1"           # 简介为空时用标题


def test_malformed_llm_item_degrades_that_event_only(store, settings):
    ids = [_add(store, n, "Story %d" % n, summary="Sum %d. More." % n) for n in range(1, 6)]
    long_detail = "长" * 2000
    llm = FakeLLM({"summarize": lambda s, u: {"items": [
        _item(1),
        {"i": 2, "title_zh": "", "summary_zh": "x", "detail_zh": "y"},         # 空标题
        {"i": 3, "title_zh": "标题", "summary_zh": 5, "detail_zh": "y"},       # 类型错
        "garbage",
        _item(5, detail=long_detail),
        # 4 缺失
    ]}})
    out = summarize(_sel([_cand([i]) for i in ids]), store, llm, settings)
    assert [e.degraded for e in out] == [False, True, True, True, False]
    assert out[1].title_zh == "Story 2" and out[1].summary_zh == "Sum 2."
    assert len(out[4].detail_zh) <= 800                   # 过长截断而非拒绝


def test_non_dict_reply_degrades_batch(store, settings):
    a = _add(store, 1, "Story", summary="Sum.")
    out = summarize(_sel([_cand([a])]), store, FakeLLM({"summarize": lambda s, u: {"oops": 1}}), settings)
    assert out[0].degraded and out[0].title_zh == "Story"


def test_empty_selection_makes_no_calls(store, settings):
    llm = FakeLLM({"summarize": _echo()})
    assert summarize(_sel([]), store, llm, settings) == []
    assert llm.calls == []
