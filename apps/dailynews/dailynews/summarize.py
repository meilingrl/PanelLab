"""中文摘要：只依据抓到的正文或标题简介，为选中的事件生成中文标题、摘要、详情与新变化。

大模型失败时退回原标题和简介首句（degraded=True）；遇到 BudgetExceeded 后其余批次不再调用。
"""
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .llm import BudgetExceeded, LLMError
from .rank import Candidate, Selection
from .settings import Settings
from .store import Article

BATCH = 6                  # 每次 summarize 调用最多的事件数
MAX_ARTICLES = 4           # 每个事件给大模型看的文章数
TEXT_CHARS = 1500          # 每篇依据文字的上限
TITLE_MAX = 80
SUMMARY_MAX = 300
DETAIL_MAX = 800
CHANGE_MAX = 200

SYSTEM_PROMPT = """任务：summarize
你是新闻编辑，为下面每个事件写中文简报。每个事件给出一到四篇报道的来源、原标题和原文（正文或简介）。
规则：
1. 只依据给出的原文与片段，不添加原文没有的事实、数字、人名或背景；不推测，不评论，语气中立。
2. 输出简体中文；外文人名、地名、机构名用通行的标准中文译名。
3. title_zh：中文标题，简洁准确。
4. summary_zh：2 到 3 句话概括事件。
5. detail_zh：不超过 6 句，综合多个来源的内容。
6. 标为“后续”（followup）的事件另给 change_zh：一句话，只说明今天的原文中相对“此前报道”的新变化；其他事件 change_zh 为 null。
7. 标为“仅有标题”的事件没有原文可依据：summary_zh 只写标题的中文译文，detail_zh 为空字符串，不扩写。
只输出 JSON：{"items": [{"i": 编号, "title_zh": "...", "summary_zh": "...", "detail_zh": "...", "change_zh": "..." 或 null}, ...]}，
每个事件一项，i 为该事件的编号。"""


@dataclass(frozen=True)
class SourceLink:
    name: str
    url: str


@dataclass(frozen=True)
class EventOut:
    title_zh: str
    title_orig: str
    category: str
    status: str                    # "new" | "followup"
    summary_zh: str
    detail_zh: str
    change_zh: Optional[str]
    sources: List[SourceLink]
    prev_issue: Optional[str]
    degraded: bool
    basis: str                     # "body" | "snippet"
    section: str                   # "top" | "category"
    score: float
    article_ids: Tuple[int, ...]
    prev_event_id: Optional[int]


_HAN = re.compile("[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
TITLE_ONLY_NOTE = "（此事件仅有标题：summary_zh 只写标题的中文译文，detail_zh 为空字符串）"
NO_TEXT = "（无，仅有标题）"

_SENTENCE = re.compile(r"^.*?(?:[。！？!?]|\.(?=\s|$))", re.S)


def _first_sentence(text: str) -> str:
    text = " ".join(text.split())
    m = _SENTENCE.match(text)
    return (m.group(0) if m else text)[:SUMMARY_MAX]


def _grounding(a: Article) -> Tuple[str, bool]:
    if a.body_status == "ok" and a.body_text:
        return a.body_text[:TEXT_CHARS], True
    return (a.summary or "")[:TEXT_CHARS], False


def _sources(arts: List[Article], names: Dict[str, str]) -> List[SourceLink]:
    out, seen = [], set()
    for a in arts:
        url = (a.url or "").strip()
        if url in seen or not url.lower().startswith(("http://", "https://")):
            continue
        seen.add(url)
        out.append(SourceLink(names.get(a.source_id, a.source_id), url))
    return out


def _has_text(a: Article) -> bool:
    return bool(_grounding(a)[0].strip())


def _prompt_event(n: int, c: Candidate, arts: List[Article], names: Dict[str, str],
                  prev_report: Optional[Tuple[str, str]], title_only: bool) -> str:
    head = "[%d]%s" % (n, " 后续（followup）" if c.linked.status == "followup" else "")
    lines = [head]
    if prev_report is not None:
        lines.append("此前报道：" + "；".join(t for t in prev_report if t))
    if title_only:
        lines.append(TITLE_ONLY_NOTE)
    for a in arts[:MAX_ARTICLES]:
        text = _grounding(a)[0] if _has_text(a) else NO_TEXT
        lines.append("- 来源：%s\n  原标题：%s\n  原文：%s" % (names.get(a.source_id, a.source_id), a.title, text))
    return "\n".join(lines)


def _text(item: dict, key: str, cap: int) -> Optional[str]:
    v = item.get(key)
    if not isinstance(v, str) or not v.strip():
        return None
    return v.strip()[:cap]


def _valid_item(item, size: int):
    """合格则返回 (编号, 标题, 摘要, 详情, 新变化或 None)，否则 None。followup 的 change_zh 由调用方检查。"""
    if not isinstance(item, dict):
        return None
    i = item.get("i")
    if isinstance(i, bool) or not isinstance(i, int) or not 1 <= i <= size:
        return None
    title, summary = _text(item, "title_zh", TITLE_MAX), _text(item, "summary_zh", SUMMARY_MAX)
    detail = item.get("detail_zh")
    if title is None or summary is None or not isinstance(detail, str):
        return None
    if not _HAN.search(title) or not _HAN.search(summary):     # 不是中文输出：该事件降级
        return None
    return i, title, summary, detail.strip()[:DETAIL_MAX], _text(item, "change_zh", CHANGE_MAX)


def summarize(selection: Selection, store, llm, settings: Settings) -> List[EventOut]:
    names = {s.id: s.name for s in settings.sources}
    placed = [(c, "top") for c in selection.top]
    for cat in settings.categories:
        placed.extend((c, "category") for c in selection.by_category.get(cat.id, []))

    result = []     # type: List[EventOut]
    infos = []      # 与 result 对应的 (Candidate, 文章列表, 此前报道, 是否仅有标题)
    for c, section in placed:
        arts = store.get_articles(list(c.linked.article_ids))
        first = arts[0] if arts else None
        title = first.title if first else ""
        prev = c.linked.prev_event_id
        basis = "body" if any(_grounding(a)[1] for a in arts[:MAX_ARTICLES]) else "snippet"
        result.append(EventOut(
            title_zh=title, title_orig=title, category=c.category, status=c.linked.status,
            summary_zh=_first_sentence(first.summary) if first and first.summary.strip() else title,
            detail_zh="", change_zh=None, sources=_sources(arts, names),
            prev_issue=store.event_issue_date(prev) if prev is not None else None,
            degraded=True, basis=basis, section=section, score=c.score,
            article_ids=tuple(c.linked.article_ids), prev_event_id=prev))
        title_only = not any(_has_text(a) for a in arts[:MAX_ARTICLES])
        infos.append((c, arts, store.event_report(prev) if prev is not None else None, title_only))

    for start in range(0, len(placed), BATCH):
        batch = list(range(start, min(start + BATCH, len(placed))))
        user = "事件：\n" + "\n\n".join(
            _prompt_event(n, infos[k][0], infos[k][1], names, infos[k][2], infos[k][3])
            for n, k in enumerate(batch, 1))
        try:
            data = llm.chat_json(SYSTEM_PROMPT, user, max_tokens=4000)
        except BudgetExceeded:
            break                   # 其余批次保持降级，不再调用
        except LLMError:
            continue
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            continue
        seen = set()
        for item in items:
            v = _valid_item(item, len(batch))
            if v is None or v[0] in seen:
                continue
            k = batch[v[0] - 1]
            followup = result[k].status == "followup"
            if followup and v[4] is None:
                continue
            seen.add(v[0])
            old = result[k]
            title_only = infos[k][3]          # 没有原文：摘要只是标题译文，不要详情
            result[k] = EventOut(
                title_zh=v[1], title_orig=old.title_orig, category=old.category, status=old.status,
                summary_zh=v[1] if title_only else v[2], detail_zh="" if title_only else v[3],
                change_zh=v[4] if followup else None,
                sources=old.sources, prev_issue=old.prev_issue, degraded=False, basis=old.basis,
                section=old.section, score=old.score, article_ids=old.article_ids,
                prev_event_id=old.prev_event_id)
    return result
