"""静态网站生成：每期页面、首页（跳到最新一期）、往期归档、带内容哈希的 CSS/JS。

所有外来文本 html.escape；链接只输出 http/https。每个页面文件（期内 index.html、issue.json，
以及 site/index.html、archive.html）都先写同目录临时文件再 os.replace，读者任何时刻看到的都是完整页面。
目录 0755、文件 0644，nginx 可读。
"""
import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from html import escape
from string import Template
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

from .summarize import EventOut

TEMPLATES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
WEEKDAYS = "一二三四五六日"
ISSUE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PUBLISHED = ("ok", "degraded")
SUMMARY_SOURCES = 3        # 摘要下方直接列出的来源数，其余在展开里
DIR_MODE = 0o755
FILE_MODE = 0o644


@dataclass(frozen=True)
class HiddenItem:
    title_zh: str
    prev_issue: Optional[str]


# ---------- 小工具 ----------

def _e(s) -> str:
    return escape(s or "", quote=True)


def _safe_url(url) -> Optional[str]:
    u = (url or "").strip()
    try:
        p = urlparse(u)
    except ValueError:
        return None
    if p.scheme.lower() not in ("http", "https") or not p.netloc:
        return None
    return u


def _link(text: str, url, cls: str = "") -> str:
    u = _safe_url(url)
    c = ' class="%s"' % cls if cls else ""
    if u is None:
        return "<span%s>%s</span>" % (c, _e(text))
    return '<a%s href="%s" target="_blank" rel="noopener noreferrer">%s</a>' % (c, _e(u), _e(text))


def _issue_date(s) -> Optional[date]:
    if not isinstance(s, str) or not ISSUE_RE.match(s):
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _long_date(d: date) -> str:
    return "%d年%d月%d日 星期%s" % (d.year, d.month, d.day, WEEKDAYS[d.weekday()])


def _short_date(d: date) -> str:
    return "%d月%d日" % (d.month, d.day)


def _template(name: str) -> Template:
    with open(os.path.join(TEMPLATES, name), encoding="utf-8") as f:
        return Template(f.read())


def _write_file(path: str, data: bytes) -> None:
    with open(path, "wb") as f:
        f.write(data)
    os.chmod(path, FILE_MODE)


def _ensure_dir(path: str) -> None:
    if not os.path.isdir(path):
        os.makedirs(path)
        os.chmod(path, DIR_MODE)


def _replace_file(path: str, data: bytes) -> None:
    """写临时文件再 os.replace，读者不会看到写了一半的文件。"""
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=d)
    os.close(fd)
    try:
        _write_file(tmp, data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _publish_assets(site_dir: str) -> Dict[str, str]:
    """把 style.css、app.js 以内容哈希命名放进 site/assets/；返回 {"css": 文件名, "js": 文件名}。"""
    assets = os.path.join(site_dir, "assets")
    _ensure_dir(assets)
    names = {}
    for key, src, stem, ext in (("css", "style.css", "style", "css"), ("js", "app.js", "app", "js")):
        with open(os.path.join(TEMPLATES, src), "rb") as f:
            data = f.read()
        name = "%s.%s.%s" % (stem, hashlib.sha256(data).hexdigest()[:8], ext)
        if not os.path.exists(os.path.join(assets, name)):
            _replace_file(os.path.join(assets, name), data)
        names[key] = name
    return names


def _page(template: str, prefix: str, assets: Dict[str, str], theme: str, **values) -> bytes:
    values.update(css="%sassets/%s" % (prefix, assets["css"]), js="%sassets/%s" % (prefix, assets["js"]),
                  default_theme=theme if theme in ("light", "dark") else "light")
    return _template(template).substitute(values).encode("utf-8")


# ---------- 每期页面 ----------

def _event_html(ev: EventOut, cat_names: Dict[str, str], show_cat: bool) -> str:
    tags = []
    if show_cat:
        tags.append('<span class="tag tag-cat">%s</span>' % _e(cat_names.get(ev.category, ev.category)))
    if ev.status == "followup":
        tags.append('<span class="tag tag-followup">后续</span>')
    if ev.degraded:
        tags.append('<span class="tag tag-warn">未生成摘要</span>')
    if ev.basis == "snippet":
        tags.append('<span class="tag tag-muted">仅据标题和简介</span>')
    parts = ['<article class="item" data-cat="%s">' % _e(ev.category)]
    if tags:
        parts.append('<div class="tags">%s</div>' % "".join(tags))
    parts.append('<h3 class="title">%s</h3>' % _e(ev.title_zh))
    if ev.status == "followup" and (ev.change_zh or ev.prev_issue):
        bits = ['<span class="change-label">新变化</span>%s' % _e(ev.change_zh)] if ev.change_zh else []
        prev = _issue_date(ev.prev_issue)
        if prev is not None:
            bits.append('<a class="prev" href="../%s/">前情：%s</a>' % (prev.isoformat(), _short_date(prev)))
        if bits:
            parts.append('<p class="change">%s</p>' % " ".join(bits))
    if ev.summary_zh:
        parts.append('<p class="summary">%s</p>' % _e(ev.summary_zh))

    srcs = list(ev.sources)
    if srcs:
        shown = [_link(s.name, s.url) for s in srcs[:SUMMARY_SOURCES]]
        more = len(srcs) - SUMMARY_SOURCES
        tail = '<span class="more-src">等 %d 个来源</span>' % len(srcs) if more > 0 else ""
        parts.append('<p class="sources">%s%s</p>' % ('<span class="sep">·</span>'.join(shown), tail))

    body = []
    if ev.detail_zh:
        body.append('<p class="detail">%s</p>' % _e(ev.detail_zh))
    if ev.title_orig and ev.title_orig.strip() != (ev.title_zh or "").strip():
        body.append('<p class="orig"><span class="k">原标题</span>%s</p>' % _e(ev.title_orig))
    if srcs:
        items = []
        for s in srcs:
            u = _safe_url(s.url)
            host = urlparse(u).netloc if u else ""
            items.append('<li>%s%s</li>' % (_link(s.name, s.url),
                                             ' <span class="host">%s</span>' % _e(host) if host else ""))
        body.append('<div class="links"><span class="k">全部来源</span><ul>%s</ul></div>' % "".join(items))
    if body:
        parts.append('<details class="more"><summary><span class="open-l">展开</span>'
                     '<span class="close-l">收起</span></summary><div class="more-body">%s</div></details>'
                     % "".join(body))
    parts.append("</article>")
    return "".join(parts)


def _section(sid: str, title: str, events: List[EventOut], cat_names: Dict[str, str], show_cat: bool) -> str:
    items = "".join(_event_html(e, cat_names, show_cat) for e in events)
    return ('<section class="section" id="sec-%s"><h2><span>%s</span><span class="count">%d</span></h2>'
            '<div class="items">%s</div></section>' % (_e(sid), _e(title), len(events), items))


def _neighbours(site_dir: str, issue_date: date) -> Tuple[Optional[date], Optional[date]]:
    """现有站点目录里紧邻的前一期和后一期（只算已发布、含 index.html 的期目录）。"""
    prev = nxt = None
    try:
        names = os.listdir(site_dir)
    except OSError:
        names = []
    for n in names:
        d = _issue_date(n)
        if d is None or d == issue_date or not os.path.isfile(os.path.join(site_dir, n, "index.html")):
            continue
        if d < issue_date and (prev is None or d > prev):
            prev = d
        if d > issue_date and (nxt is None or d < nxt):
            nxt = d
    return prev, nxt


def _issue_html(issue_date: date, events: List[EventOut], hidden: List[HiddenItem], settings,
                site_dir: str, assets: Dict[str, str]) -> bytes:
    cat_names = {c.id: c.name for c in settings.categories}
    order = [c.id for c in settings.categories]
    top = [e for e in events if e.section == "top"]
    rest = [e for e in events if e.section != "top"]
    extra = []
    for e in rest:
        if e.category not in cat_names and e.category not in extra:
            extra.append(e.category)

    sections = []
    if top:
        sections.append(_section("top", "要闻", top, cat_names, True))
    for cid in order + extra:
        evs = [e for e in rest if e.category == cid]
        if evs:
            sections.append(_section(cid, cat_names.get(cid, cid), evs, cat_names, False))

    if events:
        present = {e.category for e in events}
        chips = ['<button type="button" class="chip" data-filter="all" aria-pressed="true">全部</button>']
        for cid in order + extra:
            if cid in present:
                chips.append('<button type="button" class="chip" data-filter="%s" aria-pressed="false">%s</button>'
                             % (_e(cid), _e(cat_names.get(cid, cid))))
        filters = '<nav class="filters" aria-label="类别筛选" hidden><div class="chips">%s</div></nav>' % "".join(chips)
        main = "".join(sections) + '<p class="filter-empty" hidden>这个类别今天没有条目</p>'
    else:
        filters = ""
        main = '<p class="empty">今天没有新的要闻</p>'

    if hidden:
        lis = []
        for h in hidden:
            d = _issue_date(h.prev_issue)
            link = ' <a href="../%s/">%s</a>' % (d.isoformat(), _short_date(d)) if d else ""
            lis.append("<li>%s%s</li>" % (_e(h.title_zh), link))
        hidden_html = ('<details class="hidden-list"><summary>已隐藏 %d 条旧闻</summary>'
                       '<p class="note">这些事件近几天已出现过，今天没有新进展。</p><ul>%s</ul></details>'
                       % (len(hidden), "".join(lis)))
    else:
        hidden_html = ""

    prev, nxt = _neighbours(site_dir, issue_date)
    nav = []
    nav.append('<a href="../%s/">‹ 上一期</a>' % prev.isoformat() if prev else '<span class="off">‹ 上一期</span>')
    nav.append('<a href="../archive.html">往期</a>')
    nav.append('<a href="../%s/">下一期 ›</a>' % nxt.isoformat() if nxt else '<span class="off">下一期 ›</span>')

    count = "%d 条" % len(events) if events else "无新条目"
    return _page("page.html", "../", assets, settings.default_theme,
                 title=_e("每日简报 · %s" % _short_date(issue_date)),
                 date_long=_e(_long_date(issue_date)),
                 count=_e(count),
                 nav="".join(nav),
                 filters=filters,
                 main=main,
                 hidden=hidden_html)


def _issue_json(issue_date: date, events: List[EventOut], hidden: List[HiddenItem]) -> bytes:
    data = {
        "date": issue_date.isoformat(),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "events": [asdict(e) for e in events],
        "hidden": [asdict(h) for h in hidden],
    }
    return json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")


def _sweep(path: str) -> None:
    """删除 path 下直接的 .tmp-* / .old-* 残留（上次中断留下的临时文件或目录）。"""
    try:
        names = os.listdir(path)
    except OSError:
        return
    for n in names:
        if not n.startswith((".tmp-", ".old-")):
            continue
        p = os.path.join(path, n)
        if os.path.isdir(p) and not os.path.islink(p):
            shutil.rmtree(p, ignore_errors=True)
        else:
            try:
                os.unlink(p)
            except OSError:
                pass


def render_issue(issue_date: date, events: List[EventOut], hidden: List[HiddenItem], settings,
                 site_dir: str) -> None:
    """生成 site/<YYYY-MM-DD>/index.html 与 issue.json。

    每个文件先写到同目录的临时文件再 os.replace 到位：读者任何时刻读到的都是某一版完整的文件，
    期目录本身一直存在。
    """
    _ensure_dir(site_dir)
    _sweep(site_dir)
    target = os.path.join(site_dir, issue_date.isoformat())
    _ensure_dir(target)
    _sweep(target)
    assets = _publish_assets(site_dir)
    html = _issue_html(issue_date, events, hidden, settings, site_dir, assets)
    _replace_file(os.path.join(target, "issue.json"), _issue_json(issue_date, events, hidden))
    _replace_file(os.path.join(target, "index.html"), html)


# ---------- 首页与归档 ----------

def render_index(store, site_dir: str) -> None:
    """site/index.html 跳到最新一期（ok|degraded）；site/archive.html 按月列出各期。"""
    _ensure_dir(site_dir)
    _sweep(site_dir)
    assets = _publish_assets(site_dir)
    issues = [r for r in store.list_issues() if r.status in PUBLISHED and _issue_date(r.date)]

    months = []  # [(label, [rows])]
    for r in issues:
        d = _issue_date(r.date)
        label = "%d年%d月" % (d.year, d.month)
        if not months or months[-1][0] != label:
            months.append((label, []))
        months[-1][1].append(r)
    if months:
        blocks = []
        for label, rows in months:
            lis = []
            for r in rows:
                d = _issue_date(r.date)
                note = ' <span class="tag tag-warn">部分降级</span>' if r.status == "degraded" else ""
                lis.append('<li><a href="%s/"><span class="d">%s</span><span class="w">星期%s</span></a>'
                           '<span class="n">%d 条</span>%s</li>'
                           % (d.isoformat(), _short_date(d), WEEKDAYS[d.weekday()], r.item_count, note))
            blocks.append('<section class="month"><h2>%s</h2><ul class="issues">%s</ul></section>'
                          % (_e(label), "".join(lis)))
        body = "".join(blocks)
    else:
        body = '<p class="empty">还没有简报</p>'
    theme = "light"
    archive = _page("archive.html", "", assets, theme, title="往期 · 每日简报", heading="往期",
                    head_extra="", body=body)

    if issues:
        latest = issues[0].date
        index = _page("archive.html", "", assets, theme, title="每日简报", heading="每日简报",
                      head_extra='<meta http-equiv="refresh" content="0; url=%s/">' % latest,
                      body='<p class="empty"><a href="%s/">打开最新一期（%s）</a></p>'
                           % (latest, _short_date(_issue_date(latest))))
    else:
        index = _page("archive.html", "", assets, theme, title="每日简报", heading="每日简报",
                      head_extra="", body='<p class="empty">还没有简报</p>')
    _replace_file(os.path.join(site_dir, "archive.html"), archive)
    _replace_file(os.path.join(site_dir, "index.html"), index)
