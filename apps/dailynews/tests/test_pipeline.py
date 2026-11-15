import json
import os
import re
from datetime import date, datetime, timedelta, timezone

import pytest

from conftest import FakeLLM, fixture_bytes, raw_settings
from dailynews import cli, pipeline
from dailynews.feeds import CollectReport
from dailynews.llm import BudgetExceeded, LLMError
from dailynews.settings import load_settings
from dailynews.store import Store

CST = timezone(timedelta(hours=8))
DAY1 = datetime(2026, 11, 12, 23, 20, tzinfo=CST)       # 期号 2026-11-13
DAY2 = datetime(2026, 11, 13, 23, 20, tzinfo=CST)       # 期号 2026-11-14
SECRET = "sk-test-SECRET-0123456789"


# ---------- 夹具 ----------

def rss(server, items):
    """items: [(标题, 文章路径, RFC822 时间, 简介)]；链接都指向本地服务器。"""
    parts = ['<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>t</title>']
    for title, path, pub, desc in items:
        parts.append("<item><title>%s</title><link>%s</link><pubDate>%s</pubDate>"
                     "<description>%s</description></item>" % (title, server.url(path), pub, desc))
    parts.append("</channel></rss>")
    return "".join(parts).encode("utf-8")


def write_settings(tmp_path, sources, **extra):
    raw = raw_settings(sources)
    raw["site_dir"] = str(tmp_path / "site")
    raw["data_dir"] = str(tmp_path / "data")
    raw.update(extra)
    p = tmp_path / "settings.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    return str(p)


def two_sources(server):
    return [{"id": "bbc", "name": "BBC News", "url": server.url("/bbc.xml"), "kind": "rss", "type": "agency"},
            {"id": "gd", "name": "The Guardian", "url": server.url("/gd.xml"), "kind": "rss"}]


PUB1 = "Thu, 12 Nov 2026 12:00:00 GMT"
PUB2 = "Fri, 13 Nov 2026 12:00:00 GMT"

BBC_DAY1 = [("Floods hit southern region", "/a/flood-bbc", PUB1, "Heavy rain has flooded towns."),
            ("Election results delayed in capital", "/a/elect-bbc", PUB1, "Counting continues amid dispute."),
            ("Provincial officials announce local road plan", "/a/prov", PUB1, "A province plans roads.")]
GD_DAY1 = [("Southern region floods leave thousands displaced", "/a/flood-gd", PUB1, "Thousands displaced."),
           ("Chip maker unveils faster processor", "/a/chip", PUB1, "A new processor is announced.")]
BBC_DAY2 = BBC_DAY1 + [
    ("Floods hit southern region again", "/a/flood-bbc-2", PUB2, "Rain continues over the region."),
    ("Election results announced after long delay in capital", "/a/elect-bbc-2", PUB2, "Winner declared."),
]
GD_DAY2 = GD_DAY1 + [("Markets rally on surprise rate cut", "/a/markets", PUB2, "Stocks rose sharply.")]


def serve(server, bbc, gd):
    server.route("/bbc.xml", body=rss(server, bbc))
    server.route("/gd.xml", body=rss(server, gd))
    for items in (bbc, gd):
        for _, path, _, _ in items:
            server.route(path, body=fixture_bytes("article_bbc.html"), content_type="text/html; charset=utf-8")


def _numbered_titles(user):
    return [(int(n), t.strip()) for n, t in re.findall(r"^\[(\d+)\] (.*)$", user, re.M)]


def _cluster(system, user):
    """标题里含同一关键词的条目归为一组。"""
    groups, by_key = [], {}
    for n, title in _numbered_titles(user):
        low = title.lower()
        key = "flood" if "flood" in low else ("election" if "election" in low else None)
        if key is None:
            groups.append([n])
        elif key in by_key:
            by_key[key].append(n)
        else:
            by_key[key] = [n]
            groups.append(by_key[key])
    return {"groups": groups}


def _link(system, user):
    results = []
    for block in user.split("\n\n"):
        m = re.match(r"第 (\d+) 组", block)
        if not m:
            continue
        today = block.split("候选往期事件：")[0].lower()
        ids = [int(x) for x in re.findall(r"事件编号 (\d+)", block)]
        if "again" in today and ids:
            results.append({"group": int(m.group(1)), "decision": "stale", "event_id": ids[0]})
        elif "announced" in today and ids:
            results.append({"group": int(m.group(1)), "decision": "followup", "event_id": ids[0]})
        else:
            results.append({"group": int(m.group(1)), "decision": "new", "event_id": None})
    return {"results": results}


def _classify(system, user):
    items = []
    for block in re.split(r"(?m)^(?=\[\d+\])", user.split("事件组：", 1)[1]):
        m = re.match(r"\[(\d+)\]", block)
        if m:
            items.append({"i": int(m.group(1)), "category": "world",
                          "china_domestic": "provincial" in block.lower(), "importance": 3})
    return {"items": items}


def _summarize(system, user):
    items = []
    for block in re.split(r"(?m)^(?=\[\d+\])", user):
        m = re.match(r"\[(\d+)\]", block)
        if not m:
            continue
        orig = re.search(r"原标题：(.*)", block).group(1).strip()
        items.append({"i": int(m.group(1)), "title_zh": "译：" + orig, "summary_zh": "第一句。第二句。",
                      "detail_zh": "详情。", "change_zh": "结果已公布。" if "followup" in block else None})
    return {"items": items}


def _no_merge(system, user):
    return {"merge": []}


def fake_llm(**over):
    funcs = {"cluster": _cluster, "link": _link, "classify": _classify, "summarize": _summarize,
             "merge": _no_merge}
    funcs.update(over)
    return FakeLLM(funcs)


@pytest.fixture
def env(tmp_path, http_server, no_politeness):
    serve(http_server, BBC_DAY1, GD_DAY1)
    settings = load_settings(write_settings(tmp_path, two_sources(http_server)))
    os.makedirs(settings.data_dir)
    store = Store(os.path.join(settings.data_dir, "dailynews.db"))
    yield settings, store, http_server
    store.close()


def events_of(store, issue):
    return store.conn.execute(
        "select id, title_zh, status, prev_event_id, first_issue, section, degraded, basis from events "
        "where issue_date = ? order by id", (issue,)).fetchall()


def issue_row(store, issue):
    return store.conn.execute("select item_count, hidden_count, status from issues where date = ?",
                              (issue,)).fetchone()


def page(settings, issue):
    with open(os.path.join(settings.site_dir, issue, "index.html"), encoding="utf-8") as f:
        return f.read()


# ---------- 期号 ----------

def test_issue_date_rule():
    d = date(2026, 11, 12)
    assert pipeline.issue_date_for(datetime(2026, 11, 12, 11, 59, tzinfo=CST)) == d
    assert pipeline.issue_date_for(datetime(2026, 11, 12, 12, 0, tzinfo=CST)) == d + timedelta(days=1)
    assert pipeline.issue_date_for(datetime(2026, 11, 12, 23, 20, tzinfo=CST)) == d + timedelta(days=1)
    assert pipeline.issue_date_for(datetime(2026, 11, 12, 0, 30, tzinfo=CST)) == d


# ---------- 端到端 ----------

def test_build_end_to_end_publishes_issue(env):
    settings, store, _ = env
    llm = fake_llm()
    res = pipeline.run_build(settings, store, llm, DAY1)
    assert res.issue_date == date(2026, 11, 13)
    assert res.exit_code == 0

    rows = events_of(store, "2026-11-13")
    titles = sorted(r[1] for r in rows)
    # 洪水两篇归为一个事件；省级道路计划被当作国内事务过滤
    assert titles == ["译：Chip maker unveils faster processor", "译：Election results delayed in capital",
                      "译：Floods hit southern region"]
    assert all(r[2] == "new" and r[4] == "2026-11-13" and r[6] == 0 for r in rows)
    flood = [r for r in rows if "Floods" in r[1]][0]
    linked = store.conn.execute("select count(*) from event_articles where event_id = ?", (flood[0],)).fetchone()
    assert linked[0] == 2
    assert any(r[7] == "body" for r in rows)
    assert issue_row(store, "2026-11-13") == (3, 0, "ok")

    html = page(settings, "2026-11-13")
    assert "译：Floods hit southern region" in html
    assert os.path.isfile(os.path.join(settings.site_dir, "2026-11-13", "issue.json"))
    with open(os.path.join(settings.site_dir, "index.html"), encoding="utf-8") as f:
        assert "2026-11-13/" in f.read()

    b = store.conn.execute("select status, issue_date from builds").fetchall()
    assert b == [("ok", "2026-11-13")]
    out = "\n".join(res.lines)
    assert "期号 2026-11-13" in out
    assert "新文章 5" in out
    assert "事件 3（后续 0，隐藏旧闻 0，过滤国内 1）" in out
    assert "来源失败：无" in out
    assert "大模型调用 %d 次" % len(llm.calls) in out


def test_second_build_same_issue_replaces_not_duplicates(env):
    settings, store, server = env
    assert pipeline.run_build(settings, store, fake_llm(), DAY1).exit_code == 0
    first = events_of(store, "2026-11-13")
    assert len(first) == 3

    # 半小时后重试（同一期）；来源多了一条
    serve(server, BBC_DAY1, GD_DAY1 + [("Markets rally on surprise rate cut", "/a/markets",
                                        "Thu, 12 Nov 2026 15:40:00 GMT", "Stocks rose.")])
    llm = fake_llm()
    res = pipeline.run_build(settings, store, llm, DAY1 + timedelta(minutes=30))
    assert res.issue_date == date(2026, 11, 13) and res.exit_code == 0
    rows = events_of(store, "2026-11-13")
    titles = [r[1] for r in rows]
    assert len(titles) == len(set(titles)) == 4          # 不重复，第一次的事件仍在
    assert "译：Markets rally on surprise rate cut" in titles
    assert all(r[2] == "new" and r[3] is None for r in rows)   # 没把自己当成昨天的旧闻
    assert not any(c[0] == "link" for c in llm.calls)
    assert store.conn.execute("select count(*) from events").fetchone()[0] == 4
    orphans = store.conn.execute(
        "select count(*) from event_articles where event_id not in (select id from events)").fetchone()[0]
    assert orphans == 0
    assert store.conn.execute("select count(*) from issues").fetchone()[0] == 1
    assert issue_row(store, "2026-11-13") == (4, 0, "ok")
    assert "已隐藏" not in page(settings, "2026-11-13")


def test_next_day_marks_followup_and_hides_stale(env):
    settings, store, server = env
    assert pipeline.run_build(settings, store, fake_llm(), DAY1).exit_code == 0
    day1 = {r[1]: r for r in events_of(store, "2026-11-13")}

    serve(server, BBC_DAY2, GD_DAY2)
    res = pipeline.run_build(settings, store, fake_llm(), DAY2)
    assert res.issue_date == date(2026, 11, 14) and res.exit_code == 0
    rows = {r[1]: r for r in events_of(store, "2026-11-14")}
    assert set(rows) == {"译：Election results announced after long delay in capital",
                         "译：Markets rally on surprise rate cut"}
    follow = rows["译：Election results announced after long delay in capital"]
    assert follow[2] == "followup"
    assert follow[3] == day1["译：Election results delayed in capital"][0]
    assert follow[4] == "2026-11-13"
    assert issue_row(store, "2026-11-14") == (2, 1, "ok")
    assert "事件 2（后续 1，隐藏旧闻 1，过滤国内 0）" in "\n".join(res.lines)

    html = page(settings, "2026-11-14")
    assert "后续" in html and "结果已公布。" in html
    assert "已隐藏 1 条旧闻" in html
    assert "译：Floods hit southern region" in html          # 隐藏项用往期事件的中文标题
    assert 'href="../2026-11-13/"' in html
    # 上一期重新生成后有「下一期」链接，内容不变
    prev = page(settings, "2026-11-13")
    assert '<a href="../2026-11-14/">下一期 ›</a>' in prev
    assert "译：Floods hit southern region" in prev and "BBC News" in prev


def test_failed_build_articles_carry_to_next_build(env, monkeypatch):
    settings, store, _ = env

    def boom(*a, **k):
        raise RuntimeError("x")

    with monkeypatch.context() as m:
        m.setattr(pipeline.rank, "classify", boom)
        assert pipeline.run_build(settings, store, fake_llm(), DAY1).exit_code == 1
    # 次日来源内容没变（文章都已入库），失败那次抓到的文章仍进入下一期
    res = pipeline.run_build(settings, store, fake_llm(), DAY2)
    assert res.exit_code == 0 and "新文章 0" in res.lines
    assert len(events_of(store, "2026-11-14")) == 3


def test_all_sources_down_publishes_empty_issue_exit_3(env):
    settings, store, server = env
    server.route("/bbc.xml", status=503)
    server.route("/gd.xml", status=503)
    res = pipeline.run_build(settings, store, fake_llm(), DAY1)
    assert res.exit_code == 3
    assert issue_row(store, "2026-11-13") == (0, 0, "degraded")
    assert "今天没有新的要闻" in page(settings, "2026-11-13")
    out = "\n".join(res.lines)
    assert "bbc" in out and "gd" in out and "503" in out
    assert store.conn.execute("select status from builds").fetchone()[0] == "degraded"


def test_budget_exceeded_exit_3(env):
    settings, store, _ = env

    def broke(system, user):
        raise BudgetExceeded("今日大模型调用已达上限（1 次）")

    llm = FakeLLM({k: broke for k in ("cluster", "link", "classify", "summarize", "merge")})
    res = pipeline.run_build(settings, store, llm, DAY1)
    assert res.exit_code == 3
    rows = events_of(store, "2026-11-13")
    assert rows and all(r[6] == 1 for r in rows)
    assert issue_row(store, "2026-11-13")[2] == "degraded"
    assert "未生成摘要" in page(settings, "2026-11-13")


def _raise(msg):
    def fn(system, user):
        raise LLMError(msg)
    return fn


def test_dead_model_exit_3(env):
    settings, store, _ = env
    dead = _raise("大模型请求失败：HTTP 401")
    llm = FakeLLM({k: dead for k in ("cluster", "link", "classify", "summarize", "merge")})
    res = pipeline.run_build(settings, store, llm, DAY1)
    assert res.exit_code == 3
    n = len(llm.calls)
    assert "大模型调用失败 %d 次" % n in res.lines
    assert "大模型调用失败 %d 次" % n in res.note
    rows = events_of(store, "2026-11-13")
    assert rows and all(r[6] == 1 for r in rows)            # 退回原文的条目仍然显示
    assert issue_row(store, "2026-11-13")[2] == "degraded"
    assert store.conn.execute("select status from builds").fetchone()[0] == "degraded"


def test_any_summarize_failure_exit_3(env):
    settings, store, _ = env
    llm = fake_llm(summarize=_raise("大模型请求失败：HTTP 502"))
    res = pipeline.run_build(settings, store, llm, DAY1)
    assert res.exit_code == 3
    assert "大模型调用失败 1 次" in res.lines
    assert issue_row(store, "2026-11-13")[2] == "degraded"


def test_minor_llm_failure_below_half_is_ok(env):
    settings, store, _ = env
    llm = fake_llm(cluster=_raise("大模型请求失败：HTTP 500"))
    res = pipeline.run_build(settings, store, llm, DAY1)
    assert len(llm.calls) >= 4                              # cluster 失败 1 次，其余成功，不到一半
    assert res.exit_code == 0
    assert not any("大模型调用失败" in line for line in res.lines)
    assert issue_row(store, "2026-11-13")[2] == "ok"


def test_merge_joins_story_split_across_cluster_batches(env):
    settings, store, _ = env

    def singletons(system, user):
        return {"groups": [[n] for n, _ in _numbered_titles(user)]}

    def merge_floods(system, user):
        nums = [n for n, t in _numbered_titles(user) if "flood" in t.lower()]
        return {"merge": [nums]}

    res = pipeline.run_build(settings, store, fake_llm(cluster=singletons, merge=merge_floods), DAY1)
    assert res.exit_code == 0
    rows = events_of(store, "2026-11-13")
    floods = [r for r in rows if "flood" in r[1].lower()]
    assert len(floods) == 1
    n = store.conn.execute("select count(*) from event_articles where event_id = ?", (floods[0][0],)).fetchone()
    assert n[0] == 2


def _fetched(server):
    return [r.path for r in server.requests if r.method == "GET" and r.path.startswith("/a/")]


def test_bodies_fetched_after_ranking_in_page_order(env):
    settings, store, server = env
    res = pipeline.run_build(settings, store, fake_llm(), DAY1)
    assert res.exit_code == 0
    fetched = _fetched(server)
    assert "/a/prov" not in fetched                          # 被过滤的国内条目不抓原文
    assert set(fetched) == {"/a/flood-bbc", "/a/flood-gd", "/a/elect-bbc", "/a/chip"}
    assert fetched[:2] == ["/a/flood-bbc", "/a/flood-gd"]      # 要闻第一条（两个来源）在前，agency 来源优先


def test_fetch_cap_goes_to_top_event(tmp_path, http_server, no_politeness):
    serve(http_server, BBC_DAY1, GD_DAY1)
    settings = load_settings(write_settings(tmp_path, two_sources(http_server), fetch_articles_max=1))
    os.makedirs(settings.data_dir)
    store = Store(os.path.join(settings.data_dir, "dailynews.db"))
    try:
        assert pipeline.run_build(settings, store, fake_llm(), DAY1).exit_code == 0
        assert _fetched(http_server) == ["/a/flood-bbc"]
        # 同一期重试：已有正文的不再抓，名额给下一篇
        assert pipeline.run_build(settings, store, fake_llm(), DAY1 + timedelta(minutes=30)).exit_code == 0
        assert _fetched(http_server) == ["/a/flood-bbc", "/a/flood-gd"]
    finally:
        store.close()


def test_since_uses_last_successful_build(env, monkeypatch):
    settings, store, _ = env
    seen = []

    def fake_collect(settings_, store_, since, now, throttle=None):
        seen.append(since)
        return CollectReport(ok=[s.id for s in settings_.sources])

    monkeypatch.setattr(pipeline.feeds, "collect", fake_collect)
    pipeline.run_build(settings, store, fake_llm(), DAY1)
    assert seen[-1] == DAY1 - timedelta(hours=36)

    ok_start = datetime(2026, 11, 13, 15, 20, tzinfo=timezone.utc)
    bid = store.start_build(ok_start, "2026-11-14")
    store.finish_build(bid, ok_start + timedelta(minutes=2), "ok")
    bid = store.start_build(ok_start + timedelta(hours=1), "2026-11-14")
    store.finish_build(bid, ok_start + timedelta(hours=1, minutes=2), "failed")
    # 上次成功 build 在 36 小时以内：仍取最近 36 小时
    now = DAY2 + timedelta(hours=2)
    pipeline.run_build(settings, store, fake_llm(), now)
    assert seen[-1] == now - timedelta(hours=36)
    # 上次成功 build 早于 36 小时（之后的 build 都失败）：从它开始
    now = ok_start + timedelta(days=3)
    for row in store.conn.execute("select id from builds where started_at > ?", ("2026-11-13T15:20:00Z",)):
        store.finish_build(row[0], now, "failed")
    pipeline.run_build(settings, store, fake_llm(), now)
    assert seen[-1] == ok_start


def test_unexpected_error_reports_stage_and_type_only(env, monkeypatch):
    settings, store, _ = env

    def boom(*a, **k):
        raise RuntimeError("secret detail " + SECRET)

    monkeypatch.setattr(pipeline.rank, "classify", boom)
    res = pipeline.run_build(settings, store, fake_llm(), DAY1)
    assert res.exit_code == 1
    assert res.lines == ["构建失败（阶段：分类）：RuntimeError"]
    assert store.conn.execute("select status from builds").fetchone()[0] == "failed"
    assert issue_row(store, "2026-11-13") is None


def test_render_failure_marks_issue_failed(env, monkeypatch):
    settings, store, _ = env

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(pipeline.render, "render_index", boom)
    res = pipeline.run_build(settings, store, fake_llm(), DAY1)
    assert res.exit_code == 1
    assert res.lines == ["构建失败（阶段：发布）：OSError"]
    assert issue_row(store, "2026-11-13")[2] == "failed"
    assert store.conn.execute("select status from builds").fetchone()[0] == "failed"


def test_recovered_source_items_published_before_last_build_are_collected(env):
    settings, store, server = env
    server.route("/gd.xml", status=500)
    assert pipeline.run_build(settings, store, fake_llm(), DAY1).exit_code == 0   # 2 个来源坏 1 个，不超过一半
    # 次日 gd 恢复，带着一条在第一天 build 之前发布的条目
    serve(server, BBC_DAY1, GD_DAY1)
    res = pipeline.run_build(settings, store, fake_llm(), DAY2)
    assert res.exit_code == 0
    titles = [r[1] for r in events_of(store, "2026-11-14")]
    assert "译：Chip maker unveils faster processor" in titles


def test_issue_row_failed_until_pages_published(env, monkeypatch):
    settings, store, _ = env
    real = pipeline.render.render_issue
    seen = []

    def check(*a, **k):
        seen.append(issue_row(store, "2026-11-13")[2])
        return real(*a, **k)

    monkeypatch.setattr(pipeline.render, "render_issue", check)
    res = pipeline.run_build(settings, store, fake_llm(), DAY1)
    assert res.exit_code == 0
    assert seen == ["failed"]
    assert issue_row(store, "2026-11-13")[2] == "ok"
    with open(os.path.join(settings.site_dir, "index.html"), encoding="utf-8") as f:
        assert "2026-11-13/" in f.read()


def test_render_issue_failure_leaves_issue_failed(env, monkeypatch):
    settings, store, _ = env

    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(pipeline.render, "render_issue", boom)
    res = pipeline.run_build(settings, store, fake_llm(), DAY1)
    assert res.exit_code == 1 and res.lines == ["构建失败（阶段：发布）：RuntimeError"]
    assert issue_row(store, "2026-11-13")[2] == "failed"


def test_previous_issue_rerender_failure_is_warning_exit_3(env, monkeypatch):
    settings, store, server = env
    assert pipeline.run_build(settings, store, fake_llm(), DAY1).exit_code == 0
    serve(server, BBC_DAY2, GD_DAY2)
    real = pipeline.render.render_issue

    def flaky(issue_date, *a, **k):
        if issue_date == date(2026, 11, 13):
            raise OSError("x")
        return real(issue_date, *a, **k)

    monkeypatch.setattr(pipeline.render, "render_issue", flaky)
    res = pipeline.run_build(settings, store, fake_llm(), DAY2)
    assert res.exit_code == 3
    assert "上一期页面更新失败：OSError" in res.lines
    assert "期号 2026-11-14" in res.lines
    assert issue_row(store, "2026-11-14")[2] == "ok"
    status = store.conn.execute("select status from builds order by id desc limit 1").fetchone()[0]
    assert status == "degraded"


# ---------- 命令行 ----------

def test_missing_api_key_exit_2(tmp_path, http_server, monkeypatch, capsys):
    path = write_settings(tmp_path, two_sources(http_server))
    monkeypatch.delenv("DAILYNEWS_LLM_API_KEY", raising=False)
    assert cli.main(["--settings", path, "build", "--now", DAY1.isoformat()]) == 2
    err = capsys.readouterr().err
    assert "缺少 DAILYNEWS_LLM_API_KEY" in err
    assert http_server.requests == []
    assert not os.path.exists(str(tmp_path / "data"))
    assert not os.path.exists(str(tmp_path / "site"))


def test_invalid_settings_exit_2(tmp_path, monkeypatch, capsys):
    p = tmp_path / "s.json"
    p.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("DAILYNEWS_LLM_API_KEY", SECRET)
    assert cli.main(["--settings", str(p), "build"]) == 2
    assert "设置不合法" in capsys.readouterr().out


def test_output_never_contains_api_key(tmp_path, http_server, no_politeness, monkeypatch, capsys):
    serve(http_server, BBC_DAY1, GD_DAY1)
    # 大模型接口拒绝请求，并在错误正文里回显密钥
    http_server.route("/v1/chat/completions", method="POST",
                      handler=lambda req: (401, {"Content-Type": "application/json"},
                                           json.dumps({"error": req.headers.get("Authorization")}).encode()))
    path = write_settings(tmp_path, two_sources(http_server),
                          llm={"base_url": http_server.url("/v1"), "model": "m"})
    monkeypatch.setenv("DAILYNEWS_LLM_API_KEY", SECRET)

    code = cli.main(["--settings", path, "build", "--now", DAY1.isoformat()])
    out = capsys.readouterr()
    assert code == 3                                     # 大模型全部失败：网页照发，退出码 3 告警
    assert "大模型调用失败" in out.out
    auth = [r for r in http_server.requests if r.method == "POST"]
    assert auth and auth[0].headers.get("Authorization") == "Bearer " + SECRET   # 密钥确实用过
    assert "期号 2026-11-13" in out.out
    assert SECRET not in out.out and SECRET not in out.err

    # 意外异常带着密钥文字时，输出也只有阶段和类型
    def boom(*a, **k):
        raise RuntimeError("Bearer " + SECRET)

    monkeypatch.setattr(pipeline.summarize, "summarize", boom)
    code = cli.main(["--settings", path, "build", "--now", (DAY1 + timedelta(minutes=10)).isoformat()])
    out = capsys.readouterr()
    assert code == 1
    assert "构建失败（阶段：摘要）：RuntimeError" in out.out
    assert SECRET not in out.out and SECRET not in out.err

    for root, _, files in os.walk(str(tmp_path)):
        for name in files:
            if name == "settings.json":
                continue
            with open(os.path.join(root, name), "rb") as f:
                assert SECRET.encode() not in f.read(), name


# ------------------------------------------------------------- 进度输出

def test_progress_reports_each_stage_once_in_order(env):
    settings, store, _ = env
    seen = []
    res = pipeline.run_build(settings, store, fake_llm(), DAY1, progress=seen.append)
    assert res.exit_code == 0
    assert seen == ["开始：采集", "开始：归并", "开始：跨天比对", "开始：分类", "开始：合并",
                    "开始：抓取原文", "开始：摘要", "开始：保存", "开始：发布", "开始：清理"]
    assert pipeline.current_stage() == "完成"


def test_progress_stops_at_the_failing_stage(env, monkeypatch):
    settings, store, _ = env
    seen = []

    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(pipeline.summarize, "summarize", boom)
    res = pipeline.run_build(settings, store, fake_llm(), DAY1, progress=seen.append)
    assert res.exit_code == 1
    assert seen[-1] == "开始：摘要"


def test_cli_build_prints_timestamped_progress_before_summary(tmp_path, http_server, no_politeness,
                                                              monkeypatch, capsys):
    serve(http_server, BBC_DAY1, GD_DAY1)
    path = write_settings(tmp_path, two_sources(http_server))
    monkeypatch.setenv("DAILYNEWS_LLM_API_KEY", SECRET)
    monkeypatch.setattr(cli, "_make_llm", lambda *a, **k: fake_llm())
    code = cli.main(["--settings", path, "build", "--now", DAY1.isoformat()])
    out = capsys.readouterr().out.splitlines()
    assert code == 0
    progress = [l for l in out if "开始：" in l]
    assert re.match(r"^\[\d\d:\d\d:\d\d\] 开始：采集$", progress[0])
    assert out.index(progress[-1]) < out.index("期号 2026-11-13")


def test_sigterm_message_names_the_stage(monkeypatch, capsys):
    monkeypatch.setattr(pipeline, "_CURRENT_STAGE", "摘要")
    with pytest.raises(SystemExit) as exc:
        cli._on_sigterm(15, None)
    assert exc.value.code == 143
    assert "构建被终止（阶段：摘要）" in capsys.readouterr().out
