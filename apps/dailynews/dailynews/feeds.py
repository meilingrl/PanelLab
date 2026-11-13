"""RSS 2.0 / Atom / HTML 列表页解析，以及把各来源采集入库。"""
import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from . import http
from .http import FetchError
from .settings import Source

MAX_HTML_ITEMS = 30
MIN_HTML_TITLE = 8


class ParseError(Exception):
    pass


@dataclass(frozen=True)
class Item:
    source_id: str
    url: str
    title: str
    summary: str
    published_at: Optional[datetime]
    lang: Optional[str]


@dataclass
class CollectReport:
    ok: List[str] = field(default_factory=list)
    failed: Dict[str, str] = field(default_factory=dict)
    new_articles: int = 0


def normalize_url(url):
    p = urlparse(url.strip())
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() != "fbclid"]
    return urlunparse((p.scheme, p.netloc, p.path, p.params, urlencode(query), ""))


class _TextStripper(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def strip_html(text):
    if not text:
        return ""
    s = _TextStripper()
    try:
        s.feed(text)
        s.close()
    except Exception:  # pragma: no cover - HTMLParser 极少抛错
        return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", text)).strip()
    return re.sub(r"\s+", " ", html.unescape("".join(s.parts))).strip()


def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _child(el, *names):
    for c in el:
        if _local(c.tag) in names:
            return c
    return None


def _text(el, *names):
    c = _child(el, *names)
    return (c.text or "").strip() if c is not None and c.text else ""


def _parse_date(value):
    if not value:
        return None
    value = value.strip()
    dt = None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        pass
    if dt is None:
        v = value.replace("Z", "+00:00").replace("z", "+00:00")
        v = re.sub(r"(\.\d+)", lambda m: (m.group(1) + "000000")[:7], v)
        try:
            dt = datetime.fromisoformat(v.replace(" ", "T", 1) if "T" not in v else v)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _http_url(url, base):
    if not url:
        return None
    full = urljoin(base, url.strip())
    p = urlparse(full)
    if p.scheme not in ("http", "https") or not p.netloc:
        return None
    return normalize_url(full)


def _parse_xml(source, body):
    head = body[:4096]
    if b"\x00" in head or head.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise ParseError("不支持的编码")
    if re.search(rb"<!\s*(DOCTYPE|ENTITY)", body, re.IGNORECASE):
        raise ParseError("拒绝含 DOCTYPE/ENTITY 声明的文档")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise ParseError("XML 解析失败：%s" % e)
    kind = _local(root.tag)
    if kind not in ("rss", "feed", "RDF"):
        raise ParseError("不是 RSS 或 Atom 文档（根元素 %s）" % kind)
    xml_lang = root.get("{http://www.w3.org/XML/1998/namespace}lang")
    channel = root if kind != "rss" else (_child(root, "channel") if _child(root, "channel") is not None else root)
    lang = _text(channel, "language") or xml_lang or None
    entries = [e for e in channel.iter() if _local(e.tag) in ("item", "entry")]
    items = []
    for e in entries:
        title = strip_html(_text(e, "title"))
        if _local(e.tag) == "entry":
            link = ""
            for c in e:
                if _local(c.tag) == "link" and c.get("rel", "alternate") == "alternate" and c.get("href"):
                    link = c.get("href")
                    break
            summary_raw = _text(e, "summary") or _text(e, "content")
            date = _text(e, "published") or _text(e, "updated")
        else:
            link = _text(e, "link")
            summary_raw = _text(e, "description") or _text(e, "encoded")
            date = _text(e, "pubDate") or _text(e, "date")
        url = _http_url(link, source.url)
        if not title or not url:
            continue
        items.append(Item(source.id, url, title, strip_html(summary_raw), _parse_date(date),
                          e.get("{http://www.w3.org/XML/1998/namespace}lang") or lang))
    return items


class _LinkCollector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []  # (href, text)
        self.lang = None
        self._href = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "html" and a.get("lang"):
            self.lang = a["lang"]
        elif tag == "a" and a.get("href"):
            self._finish()
            self._href = a["href"]
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a":
            self._finish()

    def _finish(self):
        if self._href is not None:
            self.links.append((self._href, re.sub(r"\s+", " ", "".join(self._text)).strip()))
        self._href = None
        self._text = []

    def close(self):
        super().close()
        self._finish()


def _site(host):
    host = (host or "").lower()
    return host[4:] if host.startswith("www.") else host


def _parse_html(source, body):
    text = body.decode("utf-8", "replace")
    c = _LinkCollector()
    try:
        c.feed(text)
        c.close()
    except Exception as e:
        raise ParseError("HTML 解析失败：%s" % e)
    site = _site(urlparse(source.url).hostname)
    own = normalize_url(source.url)
    items, seen = [], set()
    for href, title in c.links:
        url = _http_url(href, source.url)
        if not url or len(title) < MIN_HTML_TITLE:
            continue
        if _site(urlparse(url).hostname) != site or url == own or url in seen:
            continue
        seen.add(url)
        items.append(Item(source.id, url, title, "", None, c.lang))
        if len(items) >= MAX_HTML_ITEMS:
            break
    return items


def parse(source: Source, body: bytes, fetched_at: datetime) -> List[Item]:
    if source.kind == "html":
        return _parse_html(source, body)
    return _parse_xml(source, body)


def collect(settings, store, since, now, throttle=None):
    """逐个启用的来源抓取并入库；单个来源失败只记录，不影响其它来源。"""
    throttle = throttle or http.DomainThrottle()
    report = CollectReport()
    for source in settings.sources:
        if not source.enabled:
            continue
        try:
            throttle.wait(source.url)
            resp = http.get(source.url)
            if resp.status != 200:
                raise FetchError("HTTP %d" % resp.status)
            items = parse(source, resp.body, now)
        except (FetchError, ParseError) as e:
            report.failed[source.id] = str(e)
            continue
        except Exception as e:  # 单个来源的意外错误也不能拖垮整次采集
            report.failed[source.id] = "意外错误：%s" % e.__class__.__name__
            continue
        fresh = [i for i in items if i.published_at is None or i.published_at > since]
        report.new_articles += len(store.add_articles(fresh, fetched_at=now))
        report.ok.append(source.id)
    return report
