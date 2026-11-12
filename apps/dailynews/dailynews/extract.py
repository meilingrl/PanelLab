"""从原文 HTML 提取正文前若干段。只用标准库 html.parser，不依赖 feeds.py。"""
import codecs
import re
from html.parser import HTMLParser
from typing import List, Optional

from . import http
from .http import FetchError

MAX_CHARS = 4000
MIN_CHARS = 300
_SKIP_TAGS = frozenset(["script", "style", "nav", "header", "footer", "aside", "form",
                        "figcaption", "noscript", "template"])
_VOID_TAGS = frozenset(["area", "base", "br", "col", "embed", "hr", "img", "input", "link",
                        "meta", "param", "source", "track", "wbr"])
_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_\-:.]+)""", re.I)
_SPACE = re.compile(r"\s+")


class _BodyParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []  # (tag, node_id)
        self._next_id = 0
        self._skip = 0
        self._article = 0
        self._cur = None  # 正在收集的段落：[container_id, in_article, parts]
        self.paragraphs = []  # (container_id, in_article, text)

    def _flush(self):
        if self._cur is not None:
            text = _SPACE.sub(" ", "".join(self._cur[2])).strip()
            if text:
                self.paragraphs.append((self._cur[0], self._cur[1], text))
            self._cur = None

    def handle_starttag(self, tag, attrs):
        if tag in _VOID_TAGS:
            return
        if tag == "p":
            self._flush()
            if not self._skip:
                container = self.stack[-1][1] if self.stack else -1
                self._cur = [container, self._article > 0, []]
            return
        self._next_id += 1
        self.stack.append((tag, self._next_id))
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag == "article":
            self._article += 1

    def handle_startendtag(self, tag, attrs):
        pass  # <x/> 不入栈

    def handle_endtag(self, tag):
        if tag == "p":
            self._flush()
            return
        idx = None
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                idx = i
                break
        if idx is None:
            return
        if self._cur is not None and any(n == self._cur[0] for _, n in self.stack[idx:]):
            self._flush()  # 段落所在的容器关闭了；行内元素（a/em/strong 等）关闭不打断段落
        for t, _ in self.stack[idx:]:
            if t in _SKIP_TAGS:
                self._skip -= 1
            elif t == "article":
                self._article -= 1
        del self.stack[idx:]

    def handle_data(self, data):
        if self._cur is not None and not self._skip:
            self._cur[2].append(data)

    def close(self):
        super().close()
        self._flush()


def _decode(html_bytes, charset=None):
    if html_bytes.startswith(codecs.BOM_UTF8):
        return html_bytes[3:].decode("utf-8", "replace")
    if charset:
        try:
            return html_bytes.decode(charset, "replace")
        except (LookupError, ValueError):
            pass
    m = _META_CHARSET.search(html_bytes[:4096])
    if m:
        try:
            return html_bytes.decode(m.group(1).decode("ascii"), "replace")
        except (LookupError, ValueError):
            pass
    return html_bytes.decode("utf-8", "replace")


def _cap(paragraphs):
    out = []
    total = 0
    for p in paragraphs:
        sep = 2 if out else 0
        if total + sep + len(p) > MAX_CHARS:
            room = MAX_CHARS - total - sep
            if room > 0:
                out.append(p[:room])
            break
        out.append(p)
        total += sep + len(p)
    return "\n\n".join(out)


def extract_text(html, url, charset: Optional[str] = None) -> Optional[str]:
    parser = _BodyParser()
    try:
        parser.feed(_decode(html, charset))
        parser.close()
    except Exception:  # html.parser 对极端坏页面偶尔会抛错；当作提取失败
        return None
    in_article = [p[2] for p in parser.paragraphs if p[1]]
    if in_article:
        chosen = in_article
    else:
        by_container = {}  # type: dict
        for cid, _, text in parser.paragraphs:
            by_container.setdefault(cid, []).append(text)
        chosen = max(by_container.values(), key=lambda ps: sum(len(t) for t in ps), default=[])
    text = _cap(chosen)
    return text if len(text) >= MIN_CHARS else None


def fetch_bodies(store, settings, article_ids: List[int], throttle=None) -> None:
    """按给定顺序抓取原文并写入 body_text/body_status，最多处理 fetch_articles_max 篇。"""
    throttle = throttle or http.DomainThrottle()
    for article in store.get_articles(article_ids[:settings.fetch_articles_max]):
        try:
            throttle.wait(article.url)
            resp = http.get(article.url)
            if resp.status != 200:
                raise FetchError("HTTP %d" % resp.status)
            text = extract_text(resp.body, article.url, resp.charset)
        except Exception:  # 单篇失败（含意外错误）不能中断整批
            store.set_body(article.id, None, "failed")
            continue
        if text is None:
            store.set_body(article.id, None, "short")
        else:
            store.set_body(article.id, text, "ok")
