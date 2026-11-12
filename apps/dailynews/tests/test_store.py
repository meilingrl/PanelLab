from datetime import datetime, timedelta, timezone

from dailynews.feeds import Item
from dailynews.store import Article, Store

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)


def _item(n, published=None, source="s1"):
    return Item(source, "https://a.example/%d" % n, "Title %d" % n, "Summary %d" % n, published, "en")


def test_schema_has_all_tables(tmp_path):
    store = Store(str(tmp_path / "d.sqlite"))
    names = {r[0] for r in store.conn.execute("select name from sqlite_master where type='table'")}
    assert {"articles", "events", "event_articles", "issues", "builds", "llm_calls"} <= names
    cols = {r[1] for r in store.conn.execute("pragma table_info(events)")}
    assert {"id", "issue_date", "title_zh", "title_orig", "category", "status", "first_issue",
            "prev_event_id", "score", "summary_zh", "detail_zh", "change_zh", "degraded",
            "basis", "section"} <= cols


def test_add_articles_skips_existing_and_returns_new_ids(tmp_path):
    store = Store(str(tmp_path / "d.sqlite"))
    ids = store.add_articles([_item(1), _item(2), _item(1)], fetched_at=NOW)
    assert len(ids) == 2
    assert store.add_articles([_item(2), _item(3)], fetched_at=NOW) != []
    assert len(store.articles_since(NOW - timedelta(hours=1))) == 3


def test_articles_since_returns_article_dataclass(tmp_path):
    store = Store(str(tmp_path / "d.sqlite"))
    pub = datetime(2026, 11, 10, 8, 0, tzinfo=timezone.utc)
    store.add_articles([_item(1, pub)], fetched_at=NOW)
    store.add_articles([_item(2)], fetched_at=NOW - timedelta(days=3))
    got = store.articles_since(NOW - timedelta(hours=1))
    assert len(got) == 1
    a = got[0]
    assert isinstance(a, Article)
    assert (a.source_id, a.url, a.title, a.summary) == ("s1", "https://a.example/1", "Title 1", "Summary 1")
    assert a.published_at == pub
    assert a.fetched_at == NOW
    assert a.body_text is None and a.body_status is None


def test_last_successful_build(tmp_path):
    store = Store(str(tmp_path / "d.sqlite"))
    assert store.last_successful_build() is None
    t1 = NOW - timedelta(days=2)
    t2 = NOW - timedelta(days=1)
    b1 = store.start_build(t1, "2026-11-10")
    store.finish_build(b1, t1 + timedelta(minutes=5), "ok", "")
    b2 = store.start_build(t2, "2026-11-11")
    store.finish_build(b2, t2 + timedelta(minutes=5), "failed", "boom")
    b3 = store.start_build(NOW, "2026-11-12")  # 未结束
    last = store.last_successful_build()
    assert last.id == b1
    assert last.started_at == t1
    assert last.issue_date == "2026-11-10"
    assert last.status == "ok"
    store.finish_build(b3, NOW, "degraded", "")
    assert store.last_successful_build().id == b3


def test_prune_drops_old_articles_keeps_events(tmp_path):
    store = Store(str(tmp_path / "d.sqlite"))
    old = NOW - timedelta(days=31)
    recent = NOW - timedelta(days=29)
    old_id = store.add_articles([_item(1)], fetched_at=old)[0]
    new_id = store.add_articles([_item(2)], fetched_at=recent)[0]
    store.conn.execute(
        "insert into events(id, issue_date, title_zh, title_orig, category, status, score) "
        "values (1, '2026-10-10', '事件', 'Event', 'world', 'new', 1.0)")
    store.conn.executemany("insert into event_articles(event_id, article_id) values (1, ?)",
                           [(old_id,), (new_id,)])
    store.conn.commit()
    assert store.prune(NOW) == 1
    assert [r[0] for r in store.conn.execute("select id from articles")] == [new_id]
    assert store.conn.execute("select title_zh from events").fetchall() == [("事件",)]
    assert store.conn.execute("select article_id from event_articles").fetchall() == [(new_id,)]
