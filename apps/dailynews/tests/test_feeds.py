from datetime import datetime, timezone

import pytest

from conftest import fixture_bytes
from dailynews.feeds import Item, ParseError, normalize_url, parse
from dailynews.settings import Source

NOW = datetime(2026, 11, 11, 0, 0, tzinfo=timezone.utc)


def _src(kind, url, sid="s1"):
    return Source(sid, "Test", url, kind, "world", "media")


def test_parse_rss_fixture():
    items = parse(_src("rss", "https://www.bbc.co.uk/news/world"), fixture_bytes("bbc_world.xml"), NOW)
    assert [i.title for i in items] == ["Floods hit southern region", "Talks resume in capital", "Undated item"]
    first = items[0]
    assert isinstance(first, Item)
    assert first.source_id == "s1"
    assert first.url == "https://www.bbc.com/news/world-1001"
    assert first.summary == "Heavy rain has flooded towns & villages."
    assert first.published_at == datetime(2026, 11, 10, 8, 30, tzinfo=timezone.utc)
    assert items[1].published_at == datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
    assert items[1].url == "https://www.bbc.com/news/world-1002?id=7"
    assert items[2].published_at is None


def test_parse_rss_strips_escaped_html_and_reads_language():
    items = parse(_src("rss", "https://www.theguardian.com/world"), fixture_bytes("guardian_world.xml"), NOW)
    assert len(items) == 1
    assert items[0].summary == "Counting continues amid dispute."
    assert items[0].lang == "en-gb"


def test_parse_atom_fixture():
    items = parse(_src("atom", "https://blog.example.com/"), fixture_bytes("atom_sample.xml"), NOW)
    assert [i.title for i in items] == ["New model released", "Older post"]
    assert items[0].url == "https://blog.example.com/posts/model"
    assert items[0].summary == "We released it."
    assert items[0].published_at == datetime(2026, 11, 10, 9, 15, tzinfo=timezone.utc)
    assert items[1].published_at == datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)
    assert items[1].summary == "Body only"
    assert items[0].lang == "en"


def test_parse_html_listing_same_site_only():
    items = parse(_src("html", "https://www.example.com/news"), fixture_bytes("company_news.html"), NOW)
    assert [(i.title, i.url) for i in items] == [
        ("Company launches product line", "https://www.example.com/news/launch-2026"),
        ("Quarterly earnings announced", "https://www.example.com/news/earnings"),
    ]
    assert all(i.published_at is None for i in items)


def test_parse_html_limits_to_30():
    links = "".join('<a href="/n/%d">Headline number %d here</a>' % (i, i) for i in range(50))
    items = parse(_src("html", "https://www.example.com/news"), ("<html><body>%s</body></html>" % links).encode(), NOW)
    assert len(items) == 30


def test_broken_xml_raises_parse_error():
    with pytest.raises(ParseError):
        parse(_src("rss", "https://x.example/"), fixture_bytes("broken.xml"), NOW)


def test_html_error_page_as_feed_raises_parse_error():
    with pytest.raises(ParseError):
        parse(_src("rss", "https://x.example/"), b"<html><body><h1>502 Bad Gateway</h1></body></html>", NOW)


def test_entity_declarations_rejected():
    with pytest.raises(ParseError):
        parse(_src("rss", "https://x.example/"), fixture_bytes("entity.xml"), NOW)


def test_wrong_root_raises_parse_error():
    with pytest.raises(ParseError):
        parse(_src("rss", "https://x.example/"), b"<?xml version='1.0'?><foo/>", NOW)


def test_url_normalization_strips_tracking():
    assert normalize_url("https://a.example/p?utm_source=x&UTM_medium=y&fbclid=z&id=3#frag") == "https://a.example/p?id=3"
    assert normalize_url("https://a.example/p?utm_source=x") == "https://a.example/p"
    assert normalize_url("https://a.example/p#top") == "https://a.example/p"
    assert normalize_url("  https://a.example/p  ") == "https://a.example/p"


# ---- collect ----

import time as _time
from datetime import timedelta

from conftest import make_settings, raw_settings
from dailynews.feeds import CollectReport, collect
from dailynews.http import DomainThrottle
from dailynews.store import Store


def _store(tmp_path):
    return Store(str(tmp_path / "d.sqlite"))


SINCE = datetime(2026, 11, 8, 0, 0, tzinfo=timezone.utc)


def test_collect_continues_after_source_failure(tmp_path, http_server, no_politeness):
    http_server.route("/err.xml", status=500, body=b"oops")
    http_server.route("/broken.xml", body=fixture_bytes("broken.xml"), content_type="application/xml")
    http_server.route("/ok.xml", body=fixture_bytes("bbc_world.xml"), content_type="application/xml")
    settings = make_settings([
        {"id": "err", "url": http_server.url("/err.xml"), "kind": "rss"},
        {"id": "broken", "url": http_server.url("/broken.xml"), "kind": "rss"},
        {"id": "ok", "url": http_server.url("/ok.xml"), "kind": "rss"},
    ])
    store = _store(tmp_path)
    report = collect(settings, store, SINCE, NOW)
    assert isinstance(report, CollectReport)
    assert report.ok == ["ok"]
    assert set(report.failed) == {"err", "broken"}
    assert "500" in report.failed["err"]
    assert report.new_articles == 3
    assert len(store.articles_since(NOW - timedelta(hours=1))) == 3


def test_collect_survives_timeout_and_oversize(tmp_path, http_server, no_politeness, monkeypatch):
    from dailynews import http as dhttp
    monkeypatch.setattr(dhttp, "DEFAULT_TIMEOUT", 0.2)
    monkeypatch.setattr(dhttp, "DEFAULT_MAX_BYTES", 100)
    http_server.route("/slow.xml", body=b"x", delay=1.0)
    http_server.route("/big.xml", body=b"x" * 500)
    http_server.route("/ok.xml", body=b"<feed xmlns='http://www.w3.org/2005/Atom'/>")
    settings = make_settings([
        {"id": "slow", "url": http_server.url("/slow.xml"), "kind": "rss"},
        {"id": "big", "url": http_server.url("/big.xml"), "kind": "rss"},
        {"id": "ok", "url": http_server.url("/ok.xml"), "kind": "atom"},
    ])
    report = collect(settings, _store(tmp_path), SINCE, NOW)
    assert report.ok == ["ok"]
    assert set(report.failed) == {"slow", "big"}


def test_collect_skips_existing_urls(tmp_path, http_server, no_politeness):
    http_server.route("/ok.xml", body=fixture_bytes("bbc_world.xml"))
    settings = make_settings([{"id": "ok", "url": http_server.url("/ok.xml"), "kind": "rss"}])
    store = _store(tmp_path)
    assert collect(settings, store, SINCE, NOW).new_articles == 3
    again = collect(settings, store, SINCE, NOW)
    assert again.new_articles == 0
    assert again.ok == ["ok"]


def test_collect_respects_since(tmp_path, http_server, no_politeness):
    http_server.route("/ok.xml", body=fixture_bytes("bbc_world.xml"))
    settings = make_settings([{"id": "ok", "url": http_server.url("/ok.xml"), "kind": "rss"}])
    store = _store(tmp_path)
    # 只保留 11-10 00:00 之后的条目，以及没有发布时间的条目
    report = collect(settings, store, datetime(2026, 11, 10, 0, 0, tzinfo=timezone.utc), NOW)
    assert report.new_articles == 2
    titles = sorted(a.title for a in store.articles_since(NOW - timedelta(hours=1)))
    assert titles == ["Floods hit southern region", "Undated item"]


def test_collect_uses_enabled_sources_only(tmp_path, http_server, no_politeness):
    http_server.route("/ok.xml", body=fixture_bytes("bbc_world.xml"))
    settings = make_settings([
        {"id": "off", "url": http_server.url("/missing.xml"), "kind": "rss", "enabled": False},
        {"id": "ok", "url": http_server.url("/ok.xml"), "kind": "rss"},
    ])
    report = collect(settings, _store(tmp_path), SINCE, NOW)
    assert report.ok == ["ok"] and report.failed == {}
    assert all(r.path != "/missing.xml" for r in http_server.requests)


def test_collect_waits_between_requests_to_same_domain(tmp_path, http_server):
    for n in ("a", "b"):
        http_server.route("/%s.xml" % n, body=fixture_bytes("bbc_world.xml"))
    settings = make_settings([
        {"id": "a", "url": http_server.url("/a.xml"), "kind": "rss"},
        {"id": "b", "url": http_server.url("/b.xml"), "kind": "rss"},
    ])
    sleeps = []
    clock = {"t": 100.0}

    def fake_sleep(s):
        sleeps.append(s)
        clock["t"] += s

    throttle = DomainThrottle(min_interval=1.0, sleep=fake_sleep, clock=lambda: clock["t"])
    collect(settings, _store(tmp_path), SINCE, NOW, throttle=throttle)
    assert len(sleeps) == 1
    assert 0.99 <= sleeps[0] <= 1.0


def test_throttle_does_not_wait_for_different_domains():
    sleeps = []
    t = DomainThrottle(min_interval=1.0, sleep=sleeps.append, clock=lambda: 5.0)
    t.wait("https://a.example/x")
    t.wait("https://b.example/x")
    assert sleeps == []
    t.wait("https://a.example/y")
    assert sleeps == [1.0]
