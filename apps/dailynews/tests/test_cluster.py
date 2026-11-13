import re
from datetime import date, datetime, timezone

from conftest import FakeLLM
from dailynews.cluster import Linked, group_same_day, link_previous, normalize_title, title_similarity
from dailynews.feeds import Item
from dailynews.llm import BudgetExceeded, LLMError
from dailynews.store import Article, EventRow, Store

NOW = datetime(2026, 11, 12, 15, 0, tzinfo=timezone.utc)
ISSUE = date(2026, 11, 13)


def _art(i, title, url=None, source="s1", summary=""):
    return Article(i, source, url or "https://a.example/%d" % i, title, summary, None, NOW, None, None)


def _fail(system, user):
    raise LLMError("down")


def _numbers(user):
    """cluster 提示里每行开头的编号。"""
    return [int(m) for m in re.findall(r"^\[(\d+)\]", user, re.M)]


# ---------- 同日归并 ----------

def test_normalize_title_drops_source_suffix_and_punctuation():
    assert normalize_title("Fed Raises Rates, Again! - BBC News") == "fed raises rates again"
    assert normalize_title("Fed raises rates again | The Guardian") == "fed raises rates again"
    assert normalize_title("  美联储  再次加息！ ") == "美联储 再次加息"
    assert title_similarity("Fed raises rates again - BBC News", "Fed raises rates again | The Guardian") == 1.0
    # 中文按单字比较，英文按词比较：字母重合不算相似
    assert title_similarity("美联储再次加息", "美联储宣布再次加息") >= 0.6
    assert title_similarity("Senate passes budget", "Parents protest in Seattle") < 0.35


def test_same_story_from_two_sources_grouped():
    arts = [
        _art(1, "Fed raises interest rates by a quarter point - BBC News", source="bbc"),
        _art(2, "Volcano erupts in Iceland, flights cancelled", source="bbc"),
        _art(3, "Fed raises interest rates by quarter point | The Guardian", source="guardian"),
    ]
    # 大模型失败 → 保留初分组
    llm = FakeLLM({"cluster": _fail})
    assert group_same_day(arts, llm) == [[1, 3], [2]]


def test_shared_url_groups_even_with_different_titles():
    arts = [
        _art(1, "Breaking: summit ends", url="https://News.example/story?utm_source=rss#top"),
        _art(2, "Leaders sign climate pact", url="https://news.example/story"),
    ]
    assert group_same_day(arts, FakeLLM({"cluster": _fail})) == [[1, 2]]


def test_unrelated_titles_not_grouped():
    arts = [
        _art(1, "Apple unveils new iPhone with satellite messaging"),
        _art(2, "Earthquake strikes off the coast of Japan"),
        _art(3, "Premier League: Arsenal beat Chelsea 3-1"),
    ]
    assert group_same_day(arts, FakeLLM({"cluster": _fail})) == [[1], [2], [3]]


def test_llm_confirms_and_can_merge_paraphrases():
    arts = [
        _art(1, "OpenAI releases GPT-6", summary="The company announced its newest model."),
        _art(2, "New flagship model launched by ChatGPT maker"),
        _art(3, "Floods hit southern Spain"),
    ]
    llm = FakeLLM({"cluster": lambda s, u: {"groups": [[1, 2], [3]]}})
    assert group_same_day(arts, llm) == [[1, 2], [3]]
    task, system, user = llm.calls[0]
    assert task == "cluster"
    assert _numbers(user) == [1, 2, 3]
    assert "The company announced" in user


def test_llm_split_overrides_prefilter():
    arts = [
        _art(1, "Storm hits Florida coast"),
        _art(2, "Storm hits Florida coast again"),
        _art(3, "Unrelated: markets rally"),
    ]
    # 初分组会把 1、2 归一组；大模型判定拆开
    assert group_same_day(arts, FakeLLM({"cluster": _fail}))[0] == [1, 2]
    llm = FakeLLM({"cluster": lambda s, u: {"groups": [[1], [2], [3]]}})
    assert group_same_day(arts, llm) == [[1], [2], [3]]


def test_invalid_cluster_output_keeps_prefilter():
    arts = [_art(1, "Storm hits Florida coast"), _art(2, "Storm hits Florida coast again"),
            _art(3, "Markets rally")]
    bad = [
        {"groups": [[1, 2]]},             # 漏了 3
        {"groups": [[1, 2], [2, 3]]},     # 2 出现两次
        {"groups": [[1, 2], [4]]},        # 越界
        {"groups": [[1, 2], [True, 3]]},  # 非整数
        {"groups": "1,2,3"},
        {},
    ]
    for out in bad:
        llm = FakeLLM({"cluster": lambda s, u, out=out: out})
        assert group_same_day(arts, llm) == [[1, 2], [3]], out


def test_cluster_batches_at_most_40_and_keep_pregroups_whole():
    arts = []
    for i in range(1, 40):  # 39 条互不相关
        arts.append(_art(i, "Distinct headline number %d about topic %s" % (i, "xyz"[i % 3] * i)))
    # 两条同一故事放在末尾：放不进第一批（39+2>40），应整组进入第二批
    arts.append(_art(100, "Giant panda born at Berlin zoo"))
    arts.append(_art(101, "Giant panda born at Berlin zoo - BBC News"))
    seen = []

    def echo(system, user):
        nums = _numbers(user)
        seen.append(nums)
        return {"groups": [[n] for n in nums]} if len(nums) > 2 else {"groups": [nums]}

    # 初分组可能把部分“Distinct headline”连起来；这里只验证批次大小与整组不拆
    groups = group_same_day(arts, FakeLLM({"cluster": echo}))
    assert all(len(b) <= 40 for b in seen)
    assert len(seen) == 2
    assert [100, 101] in groups


def test_budget_exceeded_keeps_prefilter_and_stops_calling():
    arts = [_art(i, "Headline %d %s" % (i, "abcdefghij"[i % 10] * 12)) for i in range(50)]
    calls = []

    def over(system, user):
        calls.append(1)
        raise BudgetExceeded("cap")

    groups = group_same_day(arts, FakeLLM({"cluster": over}))
    assert sorted(i for g in groups for i in g) == sorted(a.id for a in arts)
    assert len(calls) == 1


def test_group_same_day_empty_and_single_skip_llm():
    llm = FakeLLM([])
    assert group_same_day([], llm) == []
    assert group_same_day([_art(1, "Only one")], llm) == [[1]]
    assert llm.calls == []


# ---------- 跨天比对 ----------

def _store(tmp_path):
    return Store(str(tmp_path / "d.sqlite"))


def _add_event(store, issue_date, title_orig, title_zh="", summary_zh=""):
    with store.conn:
        cur = store.conn.execute(
            "insert into events(issue_date, title_zh, title_orig, summary_zh) values (?, ?, ?, ?)",
            (issue_date, title_zh, title_orig, summary_zh))
    return cur.lastrowid


def _add_articles(store, titles):
    items = [Item("s1", "https://b.example/%s" % t.replace(" ", "-"), t, "summary of %s" % t, None, "en")
             for t in titles]
    return store.add_articles(items, fetched_at=NOW)


def test_recent_events_window_excludes_current_issue(tmp_path):
    store = _store(tmp_path)
    old = _add_event(store, "2026-11-05", "too old")
    a = _add_event(store, "2026-11-06", "in window start", "窗口起点")
    b = _add_event(store, "2026-11-12", "yesterday")
    _add_event(store, "2026-11-13", "same issue")
    rows = store.recent_events(before_issue=ISSUE, since_issue=date(2026, 11, 6))
    assert [r.id for r in rows] == [b, a]
    assert rows[1] == EventRow(a, date(2026, 11, 6), "窗口起点", "in window start", "")
    assert old not in [r.id for r in rows]


def test_followup_links_previous_event(tmp_path):
    store = _store(tmp_path)
    prev = _add_event(store, "2026-11-12", "Ceasefire talks begin in Cairo", "开罗停火谈判开始", "谈判开始。")
    ids = _add_articles(store, ["Ceasefire talks in Cairo reach draft deal"])

    def link(system, user):
        assert "开罗停火谈判开始" in user and str(prev) in user
        return {"results": [{"group": 1, "decision": "followup", "event_id": prev}]}

    llm = FakeLLM({"link": link})
    out = link_previous([ids], store, ISSUE, 7, llm)
    assert out == [Linked(tuple(ids), "followup", prev)]
    assert llm.calls[0][0] == "link"


def test_no_progress_is_stale(tmp_path):
    store = _store(tmp_path)
    prev = _add_event(store, "2026-11-11", "Tech giant announces layoffs of 10,000 staff")
    ids = _add_articles(store, ["Tech giant confirms 10,000 layoffs"])
    llm = FakeLLM({"link": lambda s, u: {"results": [{"group": 1, "decision": "stale", "event_id": prev}]}})
    assert link_previous([ids], store, ISSUE, 7, llm) == [Linked(tuple(ids), "stale", prev)]


def test_llm_new_decision_and_no_candidates(tmp_path):
    store = _store(tmp_path)
    _add_event(store, "2026-11-12", "Election results announced in Brazil")
    g1 = _add_articles(store, ["Brazil election results contested"])
    g2 = [_add_articles(store, ["Completely different: penguin colony found"])[0]]
    llm = FakeLLM({"link": lambda s, u: {"results": [{"group": 1, "decision": "new", "event_id": None}]}})
    out = link_previous([g1, g2], store, ISSUE, 7, llm)
    assert out == [Linked(tuple(g1), "new", None), Linked(tuple(g2), "new", None)]
    # 没有候选的组不送大模型
    assert len(llm.calls) == 1 and "penguin" not in llm.calls[0][2]


def test_same_issue_events_never_count_as_previous(tmp_path):
    store = _store(tmp_path)
    # 同一期重跑：当期已写入的事件不能被当作“昨天的旧闻”
    _add_event(store, "2026-11-13", "Ceasefire talks in Cairo reach draft deal")
    ids = _add_articles(store, ["Ceasefire talks in Cairo reach draft deal"])
    llm = FakeLLM({"link": lambda s, u: {"results": [{"group": 1, "decision": "stale", "event_id": 1}]}})
    assert link_previous([ids], store, ISSUE, 7, llm) == [Linked(tuple(ids), "new", None)]
    assert llm.calls == []


def test_lookback_window_respected(tmp_path):
    store = _store(tmp_path)
    _add_event(store, "2026-11-05", "Ceasefire talks in Cairo reach draft deal")
    ids = _add_articles(store, ["Ceasefire talks in Cairo reach draft deal"])
    llm = FakeLLM({"link": _fail})
    assert link_previous([ids], store, ISSUE, 7, llm) == [Linked(tuple(ids), "new", None)]


def test_llm_failure_falls_back_to_similarity(tmp_path):
    store = _store(tmp_path)
    near = _add_event(store, "2026-11-12", "Ceasefire talks in Cairo reach draft deal - BBC News")
    _add_event(store, "2026-11-12", "Ceasefire talks begin")
    g_same = _add_articles(store, ["Ceasefire talks in Cairo reach a draft deal"])
    g_loose = _add_articles(store, ["Ceasefire talks collapse overnight"])
    for responder in (_fail, lambda s, u: {"results": "nope"},
                      lambda s, u: {"results": [{"group": 1, "decision": "stale", "event_id": 99999},
                                                {"group": 2, "decision": "maybe", "event_id": None}]}):
        out = link_previous([g_same, g_loose], store, ISSUE, 7, FakeLLM({"link": responder}))
        assert out == [Linked(tuple(g_same), "stale", near), Linked(tuple(g_loose), "new", None)]


def test_link_budget_exceeded_falls_back_without_raising(tmp_path):
    store = _store(tmp_path)
    near = _add_event(store, "2026-11-12", "Ceasefire talks in Cairo reach draft deal")
    groups = [_add_articles(store, ["Ceasefire talks in Cairo reach draft deal %d" % i]) for i in range(12)]
    calls = []

    def over(system, user):
        calls.append(1)
        raise BudgetExceeded("cap")

    out = link_previous(groups, store, ISSUE, 7, FakeLLM({"link": over}))
    assert [o.status for o in out] == ["stale"] * 12
    assert all(o.prev_event_id == near for o in out)
    assert len(calls) == 1


def test_link_offers_at_most_five_candidates(tmp_path):
    store = _store(tmp_path)
    for d in range(6, 13):
        _add_event(store, "2026-11-%02d" % d, "Ceasefire talks in Cairo day %d" % d)
    ids = _add_articles(store, ["Ceasefire talks in Cairo continue"])
    seen = {}

    def link(system, user):
        seen["user"] = user
        return {"results": [{"group": 1, "decision": "new", "event_id": None}]}

    link_previous([ids], store, ISSUE, 7, FakeLLM({"link": link}))
    assert seen["user"].count("Ceasefire talks in Cairo day") == 5


def test_link_prompt_states_judgement_rules(tmp_path):
    store = _store(tmp_path)
    _add_event(store, "2026-11-12", "Ceasefire talks begin in Cairo")
    ids = _add_articles(store, ["Ceasefire talks begin in Cairo"])
    llm = FakeLLM({"link": lambda s, u: {"results": [{"group": 1, "decision": "new", "event_id": None}]}})
    link_previous([ids], store, ISSUE, 7, llm)
    system = llm.calls[0][1]
    assert system.split("\n", 1)[0] == "任务：link"
    assert "stale" in system and "followup" in system


def _offered(store, prev_title, today_title):
    """往期事件 prev_title 是否作为候选出现在今天 today_title 那组的 link 调用里。"""
    _add_event(store, "2026-11-12", prev_title)
    ids = _add_articles(store, [today_title])
    llm = FakeLLM({"link": lambda s, u: {"results": [{"group": 1, "decision": "new", "event_id": None}]}})
    link_previous([ids], store, ISSUE, 7, llm)
    return bool(llm.calls) and prev_title in llm.calls[0][2]


def test_reworded_repeats_are_offered_to_llm(tmp_path):
    pairs = [
        ("Ceasefire talks begin in Cairo", "Israel, Hamas negotiators resume truce talks in Egypt"),
        ("Tech giant announces layoffs of 10,000 staff", "Amazon to cut 10,000 jobs"),
        ("Typhoon Haiyan makes landfall in Philippines", "Death toll from Typhoon Haiyan rises to 1,200"),
    ]
    for n, (prev, today) in enumerate(pairs):
        assert _offered(Store(str(tmp_path / ("d%d.sqlite" % n))), prev, today), (prev, today)


def test_unrelated_previous_event_not_offered(tmp_path):
    assert not _offered(_store(tmp_path), "Ceasefire talks begin in Cairo", "Apple releases new iPhone")


def test_overlap_alone_never_falls_back_to_stale(tmp_path):
    store = _store(tmp_path)
    _add_event(store, "2026-11-12", "Amazon to cut 10,000 jobs")
    ids = _add_articles(store, ["Tech giant announces layoffs of 10,000 staff"])
    assert link_previous([ids], store, ISSUE, 7, FakeLLM({"link": _fail})) == [Linked(tuple(ids), "new", None)]
