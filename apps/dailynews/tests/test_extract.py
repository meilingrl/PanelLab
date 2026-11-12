from dataclasses import replace
from datetime import datetime, timezone

from conftest import fixture_bytes, make_settings
from dailynews.extract import extract_text, fetch_bodies
from dailynews.feeds import Item
from dailynews.store import Store

NOW = datetime(2026, 11, 11, 0, 0, tzinfo=timezone.utc)


def test_extracts_article_paragraphs():
    bbc = extract_text(fixture_bytes("article_bbc.html"), "https://www.bbc.com/news/world-1001")
    assert bbc.startswith("Heavy rain has flooded dozens of towns and villages")
    assert "highest total recorded" in bbc
    guardian = extract_text(fixture_bytes("article_guardian.html"), "https://www.theguardian.com/world/x")
    assert guardian.startswith("Counting of ballots continued on Wednesday")
    assert "recount in the most closely contested" in guardian
    assert "Officials say the final result" not in guardian  # 不在正文容器里的导语段


def test_skips_nav_and_scripts():
    text = extract_text(fixture_bytes("article_bbc.html"), "https://www.bbc.com/x")
    for junk in ("script paragraph", "late script", "Navigation promo", "Sign in", "Rescue teams",
                 "how to prepare", "newsletter", "Copyright"):
        assert junk not in text


def test_paywall_returns_none():
    assert extract_text(fixture_bytes("paywall.html"), "https://example.com/p") is None


def test_caps_at_4000_chars():
    para = "<p>" + ("word " * 100).strip() + ".</p>"
    html = ("<html><body><article>%s</article></body></html>" % (para * 30)).encode()
    text = extract_text(html, "https://example.com/long")
    assert 3000 < len(text) <= 4000


def test_decodes_meta_charset_and_falls_back():
    body = "<p>" + "这是一段很长的中文正文内容，用来验证字符集的识别。" * 20 + "</p>"
    gbk = ('<html><head><meta charset="gbk"></head><body><article>%s</article></body></html>' % body).encode("gbk")
    assert extract_text(gbk, "https://example.com/").startswith("这是一段很长")
    bad = b"<html><body><article><p>" + b"caf\xff " * 80 + b"</p></article></body></html>"
    assert "caf" in extract_text(bad, "https://example.com/")


def test_no_article_uses_longest_container():
    short = "<div><p>Short sidebar blurb about something.</p></div>"
    main = "<div>" + "<p>%s</p>" % ("Main story sentence goes here. " * 6) * 3 + "</div>"
    text = extract_text(("<html><body>%s%s</body></html>" % (short, main)).encode(), "https://example.com/")
    assert text.startswith("Main story sentence")
    assert "sidebar" not in text


def _store(tmp_path):
    return Store(str(tmp_path / "t.db"))


def _add(store, urls):
    return store.add_articles([Item("s1", u, "T", "", None, None) for u in urls], fetched_at=NOW)


def test_fetch_bodies_caps_count_and_records_status(tmp_path, http_server, no_politeness):
    http_server.route("/ok", body=fixture_bytes("article_bbc.html"), content_type="text/html")
    http_server.route("/short", body=fixture_bytes("paywall.html"), content_type="text/html")
    http_server.route("/err", body=b"oops", status=500)
    http_server.route("/never", body=fixture_bytes("article_bbc.html"), content_type="text/html")
    store = _store(tmp_path)
    ids = _add(store, [http_server.url(p) for p in ("/ok", "/short", "/err", "/never")] + ["http://127.0.0.1:1/dead"])
    settings = make_settings([{"id": "s1", "url": "http://127.0.0.1/feed", "kind": "rss"}])
    settings = replace(settings, fetch_articles_max=4)
    fetch_bodies(store, settings, [ids[3], ids[0], ids[1], ids[2], ids[4]])
    got = {a.id: a for a in store.get_articles(ids)}
    assert got[ids[3]].body_status == "ok" and got[ids[0]].body_status == "ok"
    assert got[ids[0]].body_text.startswith("Heavy rain")
    assert got[ids[1]].body_status == "short" and got[ids[1]].body_text is None
    assert got[ids[2]].body_status == "failed"
    assert got[ids[4]].body_status is None  # 超出上限，未处理
    assert [r.path for r in http_server.requests] == ["/never", "/ok", "/short", "/err"]


def test_fetch_bodies_marks_connection_failure(tmp_path, no_politeness):
    store = _store(tmp_path)
    ids = _add(store, ["http://127.0.0.1:1/dead"])
    settings = make_settings([{"id": "s1", "url": "http://127.0.0.1/feed", "kind": "rss"}])
    fetch_bodies(store, settings, ids)
    assert store.get_articles(ids)[0].body_status == "failed"
