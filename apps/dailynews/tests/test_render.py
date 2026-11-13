import json
import os
import re
import stat
from datetime import date

import pytest

from conftest import make_settings
from dailynews import render
from dailynews.render import HiddenItem, render_index, render_issue
from dailynews.store import Store
from dailynews.summarize import EventOut, SourceLink

DAY = date(2026, 11, 13)


@pytest.fixture
def settings():
    return make_settings([{"id": "bbc", "name": "BBC News", "url": "http://127.0.0.1/bbc", "kind": "rss"}])


@pytest.fixture
def store(tmp_path_factory):
    s = Store(str(tmp_path_factory.mktemp("db") / "d.sqlite"))
    yield s
    s.close()


def _ev(title="联合国通过决议", category="world", section="category", status="new", prev_issue=None,
        change=None, degraded=False, basis="body", sources=None, title_orig="UN passes resolution",
        summary="摘要第一句。第二句。", detail="详情。"):
    if sources is None:
        sources = [SourceLink("BBC News", "https://www.bbc.co.uk/news/1")]
    return EventOut(title_zh=title, title_orig=title_orig, category=category, status=status,
                    summary_zh=summary, detail_zh=detail, change_zh=change, sources=sources,
                    prev_issue=prev_issue, degraded=degraded, basis=basis, section=section,
                    score=5.0, article_ids=(1,), prev_event_id=None)


def _page(site, d=DAY):
    with open(os.path.join(str(site), d.isoformat(), "index.html"), encoding="utf-8") as f:
        return f.read()


def _issue(store, d, count=3, status="ok"):
    with store.conn:
        store.conn.execute("insert into issues(date, built_at, item_count, hidden_count, status) "
                           "values (?, ?, ?, 0, ?)", (d, d + "T15:30:00Z", count, status))


def test_issue_page_has_top_and_categories(tmp_path, settings):
    events = [_ev("头条一", category="ai", section="top"),
              _ev("国际新闻", category="world"),
              _ev("AI 新闻", category="ai")]
    render_issue(DAY, events, [], settings, str(tmp_path))
    html = _page(tmp_path)
    assert "每日简报" in html and "2026年11月13日 星期五" in html
    assert html.index("要闻") < html.index("头条一") < html.index("国际新闻") < html.index("AI 新闻")
    # 类别筛选：全部 + 有条目的类别
    assert 'data-filter="all"' in html and 'data-filter="world"' in html and 'data-filter="ai"' in html
    assert html.count('<article class="item"') == 3
    assert "<details" in html and "展开" in html and "UN passes resolution" in html
    assert '<meta name="robots" content="noindex">' in html
    assert 'name="viewport"' in html
    data = json.loads((tmp_path / "2026-11-13" / "issue.json").read_text(encoding="utf-8"))
    assert [e["title_zh"] for e in data["events"]] == ["头条一", "国际新闻", "AI 新闻"]
    assert "generated_at" in data and data["hidden"] == []


def test_assets_hashed_and_referenced(tmp_path, settings):
    render_issue(DAY, [_ev()], [], settings, str(tmp_path))
    names = sorted(os.listdir(str(tmp_path / "assets")))
    assert len(names) == 2
    css = [n for n in names if re.match(r"^style\.[0-9a-f]{8}\.css$", n)]
    js = [n for n in names if re.match(r"^app\.[0-9a-f]{8}\.js$", n)]
    assert css and js
    html = _page(tmp_path)
    assert 'href="../assets/%s"' % css[0] in html and 'src="../assets/%s"' % js[0] in html


def test_script_in_title_is_escaped(tmp_path, settings):
    bad = '<script>alert("x")</script> & co'
    ev = _ev(title=bad, title_orig=bad, summary=bad, detail=bad, change=bad, status="followup",
             prev_issue="2026-11-12", sources=[SourceLink(bad, "https://e.example/?a=1&b=\"2\"")])
    render_issue(DAY, [ev], [HiddenItem(bad, None)], settings, str(tmp_path))
    html = _page(tmp_path)
    assert "<script>alert" not in html
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; co" in html
    assert 'href="https://e.example/?a=1&amp;b=&quot;2&quot;"' in html


def test_javascript_url_dropped(tmp_path, settings):
    ev = _ev(sources=[SourceLink("坏来源", "javascript:alert(1)"),
                      SourceLink("数据来源", "data:text/html,hi"),
                      SourceLink("好来源", "https://ok.example/a")])
    render_issue(DAY, [ev], [], settings, str(tmp_path))
    html = _page(tmp_path)
    assert "javascript:" not in html and "data:text" not in html
    assert "坏来源" in html  # 文字保留，不加链接
    assert 'href="https://ok.example/a"' in html
    assert 'target="_blank" rel="noopener noreferrer"' in html


def test_followup_links_previous_issue(tmp_path, settings):
    ev = _ev(status="followup", prev_issue="2026-11-10", change="停火协议今天正式生效。")
    hidden = [HiddenItem("旧闻一", "2026-11-11"), HiddenItem("旧闻二", None)]
    render_issue(DAY, [ev], hidden, settings, str(tmp_path))
    html = _page(tmp_path)
    assert "后续" in html and "停火协议今天正式生效。" in html
    assert 'href="../2026-11-10/"' in html and "前情：11月10日" in html
    assert "已隐藏 2 条旧闻" in html and "旧闻一" in html and "旧闻二" in html
    assert 'href="../2026-11-11/"' in html


def test_change_label_only_when_change_text(tmp_path, settings):
    ev = _ev(status="followup", prev_issue="2026-11-10", change=None)
    render_issue(DAY, [ev], [], settings, str(tmp_path))
    html = _page(tmp_path)
    assert 'class="change-label"' not in html
    assert 'href="../2026-11-10/"' in html and "前情：11月10日" in html     # 前情链接仍在
    render_issue(DAY, [_ev(status="followup", prev_issue="2026-11-10", change="停火生效。")], [], settings,
                 str(tmp_path))
    assert '<span class="change-label">新变化</span>停火生效。' in _page(tmp_path)


def test_degraded_and_snippet_labels(tmp_path, settings):
    render_issue(DAY, [_ev(degraded=True), _ev(basis="snippet")], [], settings, str(tmp_path))
    html = _page(tmp_path)
    assert "未生成摘要" in html and "仅据标题和简介" in html


def test_bad_prev_issue_not_linked(tmp_path, settings):
    ev = _ev(status="followup", prev_issue="../../etc", change="变化。")
    render_issue(DAY, [ev], [HiddenItem("旧", "javascript:x")], settings, str(tmp_path))
    html = _page(tmp_path)
    assert "../../etc" not in html and "javascript:" not in html


def test_empty_issue_message(tmp_path, settings):
    render_issue(DAY, [], [], settings, str(tmp_path))
    html = _page(tmp_path)
    assert "今天没有新的要闻" in html
    assert "<article" not in html
    assert "已隐藏" not in html


def test_prev_next_issue_links(tmp_path, settings):
    render_issue(date(2026, 11, 11), [_ev()], [], settings, str(tmp_path))
    render_issue(date(2026, 11, 14), [_ev()], [], settings, str(tmp_path))
    render_issue(DAY, [_ev()], [], settings, str(tmp_path))
    html = _page(tmp_path)
    assert 'href="../2026-11-11/"' in html and 'href="../2026-11-14/"' in html
    assert 'href="../archive.html"' in html and "往期" in html


def test_theme_default_and_storage(tmp_path, settings):
    render_issue(DAY, [_ev()], [], settings, str(tmp_path))
    html = _page(tmp_path)
    assert 'data-theme="light"' in html
    head = html.split("</head>")[0]
    assert "localStorage" in head and "try" in head and "prefers-color-scheme: dark" in head
    js = [n for n in os.listdir(str(tmp_path / "assets")) if n.endswith(".js")][0]
    src = (tmp_path / "assets" / js).read_text(encoding="utf-8")
    assert "localStorage.setItem" in src and "try" in src


def test_index_points_to_latest(tmp_path, store, settings):
    render_index(store, str(tmp_path))
    assert "还没有简报" in (tmp_path / "index.html").read_text(encoding="utf-8")
    _issue(store, "2026-11-12")
    _issue(store, "2026-11-13", status="degraded")
    _issue(store, "2026-11-14", status="failed")
    render_index(store, str(tmp_path))
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert 'url=2026-11-13/' in html and 'href="2026-11-13/"' in html
    assert "2026-11-14" not in html


def test_archive_lists_issues(tmp_path, store):
    _issue(store, "2026-10-30", count=7)
    _issue(store, "2026-11-12", count=5)
    _issue(store, "2026-11-13", count=9)
    _issue(store, "2026-11-01", status="failed")
    assert [r.date for r in store.list_issues()] == ["2026-11-13", "2026-11-12", "2026-11-01", "2026-10-30"]
    render_index(store, str(tmp_path))
    html = (tmp_path / "archive.html").read_text(encoding="utf-8")
    assert html.index("2026年11月") < html.index('href="2026-11-13/"') < html.index('href="2026-11-12/"') \
        < html.index("2026年10月") < html.index('href="2026-10-30/"')
    assert "2026-11-01" not in html
    assert "9 条" in html


def _dot_entries(path):
    return [n for n in os.listdir(str(path)) if n.startswith(".")]


def test_publish_is_atomic(tmp_path, settings, monkeypatch):
    """新 index.html 写到一半出错 → 读者看到的仍是旧的完整页面，不留临时文件。"""
    render_issue(DAY, [_ev("第一版")], [], settings, str(tmp_path))
    before = _page(tmp_path)
    real = render._write_file

    def half_write(path, data):
        if b"\xe7\xac\xac\xe4\xba\x8c\xe7\x89\x88" in data and b"<html" in data:   # "第二版" 的页面
            with open(path, "wb") as f:
                f.write(data[: len(data) // 2])
            raise OSError("disk full")
        real(path, data)

    monkeypatch.setattr(render, "_write_file", half_write)
    with pytest.raises(OSError):
        render_issue(DAY, [_ev("第二版")], [], settings, str(tmp_path))
    assert _page(tmp_path) == before
    assert not _dot_entries(tmp_path) and not _dot_entries(tmp_path / "2026-11-13")
    monkeypatch.setattr(render, "_write_file", real)
    render_issue(DAY, [_ev("第二版")], [], settings, str(tmp_path))
    assert "第二版" in _page(tmp_path) and "第一版" not in _page(tmp_path)
    assert not _dot_entries(tmp_path) and not _dot_entries(tmp_path / "2026-11-13")


def test_failed_replace_keeps_old_issue(tmp_path, settings, monkeypatch):
    """index.html 的 os.replace 失败 → 旧页面仍完整可读；下一次 render 后不留临时文件。"""
    render_issue(DAY, [_ev("第一版")], [], settings, str(tmp_path))
    before = _page(tmp_path)
    real = os.replace

    def flaky(src, dst):
        if str(dst).endswith(os.path.join("2026-11-13", "index.html")):
            raise OSError("replace failed")
        real(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    with pytest.raises(OSError):
        render_issue(DAY, [_ev("第二版")], [], settings, str(tmp_path))
    assert _page(tmp_path) == before
    assert sorted(os.listdir(str(tmp_path))) == ["2026-11-13", "assets"]
    monkeypatch.setattr(os, "replace", real)
    render_issue(DAY, [_ev("第二版")], [], settings, str(tmp_path))
    assert "第二版" in _page(tmp_path)
    assert not _dot_entries(tmp_path) and not _dot_entries(tmp_path / "2026-11-13")


def test_stale_temp_entries_swept(tmp_path, settings, store):
    render_issue(DAY, [_ev()], [], settings, str(tmp_path))
    (tmp_path / ".old-x").mkdir()
    (tmp_path / ".old-x" / "index.html").write_text("old", encoding="utf-8")
    (tmp_path / ".tmp-y").mkdir()
    (tmp_path / ".tmp-z").write_text("half", encoding="utf-8")
    (tmp_path / "2026-11-13" / ".tmp-w").write_text("half", encoding="utf-8")
    render_issue(DAY, [_ev()], [], settings, str(tmp_path))
    assert not _dot_entries(tmp_path) and not _dot_entries(tmp_path / "2026-11-13")
    (tmp_path / ".tmp-v").mkdir()
    render_index(store, str(tmp_path))
    assert not _dot_entries(tmp_path)


def test_published_permissions(tmp_path, settings, store):
    render_issue(DAY, [_ev()], [], settings, str(tmp_path))
    render_index(store, str(tmp_path))
    for root, dirs, files in os.walk(str(tmp_path)):
        for d in dirs:
            assert stat.S_IMODE(os.stat(os.path.join(root, d)).st_mode) == 0o755
        for f in files:
            assert stat.S_IMODE(os.stat(os.path.join(root, f)).st_mode) == 0o644


def test_no_external_resources(tmp_path, settings, store):
    _issue(store, "2026-11-13")
    render_issue(DAY, [_ev()], [HiddenItem("旧闻", None)], settings, str(tmp_path))
    render_index(store, str(tmp_path))
    pages = [_page(tmp_path), (tmp_path / "index.html").read_text(encoding="utf-8"),
             (tmp_path / "archive.html").read_text(encoding="utf-8")]
    for html in pages:
        for m in re.finditer(r'<(script|link|img|iframe)\b[^>]*>', html):
            tag = m.group(0)
            assert "http" not in tag and "//" not in tag, tag
    css = [n for n in os.listdir(str(tmp_path / "assets")) if n.endswith(".css")][0]
    src = (tmp_path / "assets" / css).read_text(encoding="utf-8")
    assert "url(" not in src and "@import" not in src
