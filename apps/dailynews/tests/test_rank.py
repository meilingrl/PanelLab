import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from conftest import FakeLLM, make_settings
from dailynews.cluster import Linked
from dailynews.feeds import Item
from dailynews.llm import BudgetExceeded, LLMError
from dailynews.rank import Candidate, classify, merge_placed, select
from dailynews.store import Store

NOW = datetime(2026, 11, 12, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    s = Store(str(tmp_path / "d.sqlite"))
    yield s
    s.close()


@pytest.fixture
def settings():
    return make_settings([{"id": i, "category": c, "url": "http://127.0.0.1/%s" % i, "kind": "rss"}
                          for i, c in (("bbc", "world"), ("ars", "tech"), ("gd", "world"))])


def _add(store, n, title, source="bbc", summary=""):
    return store.add_articles([Item(source, "https://x.example/%s" % n, title, summary, None, "en")], NOW)[0]


def _answer(*rows):
    """rows: (i, category, china_domestic, importance)"""
    return lambda s, u: {"items": [{"i": i, "category": c, "china_domestic": d, "importance": m}
                                   for i, c, d, m in rows]}


def _cand(ids, category="world", dom=False, imp=3, status="new", sources=1, followed=False, blocked=False):
    return Candidate(Linked(tuple(ids), status, None), category, dom, imp, source_count=sources,
                     followed=followed, blocked=blocked)


def test_domestic_china_filtered_but_international_kept(store, settings):
    a = _add(store, 1, "Provincial official promoted in Hunan")
    b = _add(store, 2, "China and EU hold trade talks")
    llm = FakeLLM({"classify": _answer((1, "politics", True, 3), (2, "world", False, 4))})
    cands = classify([Linked((a,), "new", None), Linked((b,), "new", None)], store, settings, llm)
    assert [(c.category, c.china_domestic, c.importance) for c in cands] == [("politics", True, 3), ("world", False, 4)]
    sel = select(cands, settings)
    assert [c.linked.article_ids for c in sel.top] == [(b,)]
    assert sel.filtered == 1
    task, system, user = llm.calls[0]
    assert system.startswith("任务：classify") and "中国大陆内部事务" in system
    assert "politics：政治" in user and "China and EU hold trade talks" in user
    # 关闭过滤则保留
    sel2 = select(cands, replace(settings, filter_china_domestic=False))
    assert len(sel2.top) == 2 and sel2.filtered == 0


def test_block_keyword_removes(store, settings):
    a = _add(store, 1, "Celebrity wedding photos", summary="all about the Royal party")
    b = _add(store, 2, "Budget vote tonight")
    s = replace(settings, keywords_block=["ROYAL"])
    llm = FakeLLM({"classify": _answer((1, "world", False, 5), (2, "world", False, 2))})
    sel = select(classify([Linked((a,), "new", None), Linked((b,), "new", None)], store, s, llm), s)
    assert [c.linked.article_ids for c in sel.top] == [(b,)]
    assert sel.filtered == 1


def test_follow_keyword_boosts(store, settings):
    a = _add(store, 1, "Quantum chip announced")
    b = _add(store, 2, "Minor budget tweak")
    s = replace(settings, keywords_follow=["quantum"])
    llm = FakeLLM({"classify": _answer((1, "world", False, 3), (2, "tech", False, 2))})
    sel = select(classify([Linked((b,), "new", None), Linked((a,), "followup", 7)], store, s, llm), s)
    assert [c.linked.article_ids for c in sel.top] == [(a,), (b,)]
    assert sel.top[0].score == 2 * 1.0 + 2 and sel.top[1].score == 3.0   # followup 不加不减


def test_multi_source_bonus_capped(store, settings):
    ids = [_add(store, n, "Same story %d" % n, source=s) for n, s in enumerate(["bbc", "ars", "gd"], 1)]
    few = _cand([1], sources=2)
    many = _cand([2], sources=10)
    sel = select([few, many], settings)
    by_ids = {c.linked.article_ids: c.score for c in sel.top}
    assert by_ids[(1,)] == 3 + 0.5 and by_ids[(2,)] == 3 + 2.0
    # classify 数不同来源
    cands = classify([Linked(tuple(ids), "new", None)], store, settings, FakeLLM({"classify": _answer((1, "world", False, 3))}))
    assert cands[0].source_count == 3
    # 权重
    s = replace(settings, categories=[replace(c, weight=2.0) if c.id == "world" else c for c in settings.categories])
    assert select([_cand([1], imp=3)], s).top[0].score == 6.0


def test_top_not_repeated_in_categories(settings):
    s = replace(settings, top_count=2, per_category=2)
    cands = [_cand([n], category="world" if n < 6 else "tech", imp=1 + n % 5) for n in range(1, 9)]
    sel = select(cands, s)
    assert len(sel.top) == 2
    ids = [c.linked.article_ids for c in sel.top]
    for rows in sel.by_category.values():
        assert len(rows) <= 2
        ids += [c.linked.article_ids for c in rows]
    assert len(ids) == len(set(ids))
    assert set(sel.by_category) <= {"world", "tech"}


def test_tie_break_more_sources_then_lower_id(settings):
    a, b, c = _cand([5], sources=1, imp=3), _cand([4], sources=1, imp=3), _cand([9], sources=1, imp=2, followed=False)
    d = replace(_cand([8], sources=2, imp=2), source_count=2)   # 2 + 0.5 = 2.5 < 3
    sel = select([a, b, c, d], settings)
    assert [x.linked.article_ids[0] for x in sel.top] == [4, 5, 8, 9]


def test_invalid_llm_category_falls_back_to_source(store, settings):
    a = _add(store, 1, "Chip news", source="ars")
    b = _add(store, 2, "Other news", source="bbc")
    c = _add(store, 3, "Third", source="bbc")
    d = _add(store, 4, "Fourth", source="bbc")
    resp = {"items": [{"i": 1, "category": "nonsense", "china_domestic": True, "importance": 5},
                      {"i": 2, "category": "ai", "china_domestic": "yes", "importance": 5},
                      {"i": 3, "category": "ai", "china_domestic": True, "importance": 9},
                      {"i": 4, "category": "ai", "china_domestic": True, "importance": 4}, "junk"]}
    cands = classify([Linked((x,), "new", None) for x in (a, b, c, d)], store, settings, FakeLLM({"classify": lambda s, u: resp}))
    assert [(x.category, x.china_domestic, x.importance) for x in cands] == [
        ("tech", False, 2), ("world", False, 2), ("world", False, 2), ("ai", True, 4)]


@pytest.mark.parametrize("exc", [LLMError("down"), BudgetExceeded("cap")])
def test_llm_failure_falls_back_without_raising(store, settings, exc):
    a = _add(store, 1, "Chip news", source="ars")

    def boom(s, u):
        raise exc
    cands = classify([Linked((a,), "new", None)], store, settings, FakeLLM({"classify": boom}))
    assert [(x.category, x.china_domestic, x.importance) for x in cands] == [("tech", False, 2)]
    # 非 items 结构同样降级
    cands = classify([Linked((a,), "new", None)], store, settings, FakeLLM([{"items": "x"}]))
    assert cands[0].importance == 2


def test_batches_of_twenty(store, settings):
    ids = [_add(store, n, "Story number %d" % n) for n in range(45)]
    llm = FakeLLM({"classify": lambda s, u: {"items": []}})
    classify([Linked((i,), "new", None) for i in ids], store, settings, llm)
    assert len(llm.calls) == 3


def test_stale_goes_to_hidden(store, settings):
    a = _add(store, 1, "Old news")
    b = _add(store, 2, "Fresh news")
    llm = FakeLLM({"classify": _answer((1, "world", False, 4))})
    cands = classify([Linked((a,), "stale", 3), Linked((b,), "new", None)], store, settings, llm)
    assert "Old news" not in llm.calls[0][2]
    assert cands[0].category == "world" and cands[0].importance == 2
    sel = select(cands, settings)
    assert [c.linked.article_ids for c in sel.hidden_stale] == [(a,)]
    assert [c.linked.article_ids for c in sel.top] == [(b,)]
    assert sel.filtered == 0
    # 全是旧闻时不调用大模型
    llm2 = FakeLLM([])
    classify([Linked((a,), "stale", 3)], store, settings, llm2)
    assert llm2.calls == []


# ---------- merge（选出后的跨批合并） ----------

def _four(store, settings):
    """A>B>C>D 四个 world 事件（A、B 是同一件事，来源不同）；top 2 条、每类 1 条。"""
    s = replace(settings, top_count=2, per_category=1)
    a = _add(store, 1, "Ceasefire agreed in Gaza", summary="Negotiators agreed a ceasefire. " + "x" * 300)
    b = _add(store, 2, "Gaza truce deal reached", source="gd", summary="A truce was reached.")
    c = _add(store, 3, "Chip export rules tightened")
    d = _add(store, 4, "Rail strike called off")
    cands = [_cand([a], imp=5), _cand([b], imp=4), _cand([c], imp=3), _cand([d], imp=2)]
    return s, cands, (a, b, c, d)


def _placed(sel, settings):
    out = [c.linked.article_ids for c in sel.top]
    for cat in settings.categories:
        out.extend(c.linked.article_ids for c in sel.by_category.get(cat.id, []))
    return out


def test_merge_combines_placed_events_and_backfills(store, settings):
    s, cands, (a, b, c, d) = _four(store, settings)
    sel = select(cands, s)
    assert _placed(sel, s) == [(a,), (b,), (c,)]
    llm = FakeLLM({"merge": lambda sy, u: {"merge": [[1, 2]]}})
    out = merge_placed(sel, cands, store, s, llm)
    assert _placed(out, s) == [(a, b), (c,), (d,)]           # 合并后空出的位置由下一条补上
    merged = out.top[0]
    assert merged.source_count == 2 and merged.importance == 5 and merged.category == "world"
    assert merged.linked.status == "new" and out.filtered == sel.filtered
    task, system, user = llm.calls[0]
    assert task == "merge" and system.startswith("任务：merge\n")
    assert "[1] Ceasefire agreed in Gaza" in user and "[3] Chip export rules tightened" in user
    assert "Negotiators agreed a ceasefire." in user and "x" * 120 not in user   # 简介只取前 120 字
    assert "Rail strike" not in user                       # 只看已选出的事件


def test_merge_keeps_best_member_status_and_dedupes_ids(store, settings):
    s, cands, (a, b, c, d) = _four(store, settings)
    cands[0] = Candidate(Linked((a,), "followup", 42), "world", False, 5)
    cands[1] = _cand([b, a], imp=4)
    out = merge_placed(select(cands, s), cands, store, s, FakeLLM({"merge": lambda sy, u: {"merge": [[2, 1]]}}))
    top = out.top[0]
    assert top.linked == Linked((a, b), "followup", 42)


@pytest.mark.parametrize("reply", [
    {"merge": [[1, 4]]},              # 编号越界
    {"merge": [[1, 2], [2, 3]]},      # 编号重复
    {"merge": [[1]]},                 # 组不足两个
    {"merge": [[1, True]]},
    {"merge": [1, 2]},
    {"merge": "1,2"},
    {"groups": [[1, 2]]},
])
def test_merge_invalid_output_ignored(store, settings, reply):
    s, cands, ids = _four(store, settings)
    sel = select(cands, s)
    assert merge_placed(sel, cands, store, s, FakeLLM({"merge": lambda sy, u: reply})) == sel


@pytest.mark.parametrize("exc", [LLMError("HTTP 500"), BudgetExceeded("上限")])
def test_merge_llm_failure_ignored(store, settings, exc):
    s, cands, ids = _four(store, settings)
    sel = select(cands, s)

    def fail(sy, u):
        raise exc

    assert merge_placed(sel, cands, store, s, FakeLLM({"merge": fail})) == sel


def test_merge_skipped_with_fewer_than_two_placed(store, settings):
    a = _add(store, 1, "Only story")
    cands = [_cand([a])]
    sel = select(cands, settings)
    llm = FakeLLM({})
    assert merge_placed(sel, cands, store, settings, llm) == sel and llm.calls == []
