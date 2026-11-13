"""分类、过滤与排序：给每个事件组定类别、是否中国国内事务、重要性，再按分数选出要闻和各类别条目。

大模型失败、超出调用上限或某条输出不合格时，该条（或该批）退回降级值，这里的函数不向外抛出大模型错误。
"""
import json
from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Sequence

from .cluster import Linked
from .llm import LLMError
from .settings import Settings
from .store import Article

CLASSIFY_BATCH = 20            # 每次 classify 调用最多的组数
CLASSIFY_ARTICLES = 3          # 每组给大模型看的文章数
SUMMARY_CHARS = 200            # 提示里每条简介最多的字符数
FALLBACK_IMPORTANCE = 2
SOURCE_BONUS_STEP = 0.5        # 每多一个来源加的分
SOURCE_BONUS_CAP = 2.0
FOLLOW_BONUS = 2.0

SYSTEM_PROMPT = """任务：classify
你是新闻编辑。下面给出若干组事件（每组是同一事件的一到三篇报道，含标题、简介和来源），请为每组判断：
1. category：从允许的类别 id 中选一个最合适的。
2. china_domestic：只有当这条新闻主要是关于中国大陆内部事务时才为 true，即国内政治、地方事件、没有国际层面的国内经济或政策。
   中国的外交与对外关系、贸易争端、国际商业、以及提到中国的其他国家的新闻，一律为 false。
3. importance：重要性，1 到 5 的整数（5 最重要）。
只输出 JSON：{"items": [{"i": 编号, "category": "类别id", "china_domestic": true或false, "importance": 1到5}, ...]}，
每组一项，i 为该组的编号。"""


@dataclass(frozen=True)
class Candidate:
    """classify 填写 source_count、followed、blocked；select 填写 score（classify 返回时 score 为 0.0）。"""
    linked: Linked
    category: str
    china_domestic: bool
    importance: int
    score: float = 0.0
    source_count: int = 1
    followed: bool = False         # 命中 keywords_follow
    blocked: bool = False          # 命中 keywords_block


@dataclass(frozen=True)
class Selection:
    top: List[Candidate]
    by_category: Dict[str, List[Candidate]]    # 只含有条目的类别；不含已在 top 的事件
    hidden_stale: List[Candidate]
    filtered: int                               # 因中国国内事务或屏蔽词被去掉的组数，不含 stale


# ---------- classify ----------

def _hits(keywords: Sequence[str], text: str) -> bool:
    return any(k.strip().lower() in text for k in keywords if k.strip())


def _source_category(settings: Settings, source_id: str) -> Optional[str]:
    for s in settings.sources:
        if s.id == source_id:
            return s.category
    return None


def _prompt_group(n: int, arts: List[Article], names: Dict[str, str]) -> str:
    lines = ["[%d]" % n]
    for a in arts[:CLASSIFY_ARTICLES]:
        lines.append("- %s（来源：%s）" % (a.title, names.get(a.source_id, a.source_id)))
        if a.summary:
            lines.append("  简介：%s" % a.summary[:SUMMARY_CHARS])
    return "\n".join(lines)


def _valid_item(item, size: int, category_ids):
    """合格则返回 (编号, 类别, 国内, 重要性)，否则 None。"""
    if not isinstance(item, dict):
        return None
    i, cat, dom, imp = item.get("i"), item.get("category"), item.get("china_domestic"), item.get("importance")
    if isinstance(i, bool) or not isinstance(i, int) or not 1 <= i <= size:
        return None
    if not isinstance(cat, str) or cat not in category_ids:
        return None
    if not isinstance(dom, bool):
        return None
    if isinstance(imp, bool) or not isinstance(imp, int) or not 1 <= imp <= 5:
        return None
    return i, cat, dom, imp


def classify(groups: List[Linked], store, settings: Settings, llm) -> List[Candidate]:
    category_ids = {c.id for c in settings.categories}
    names = {s.id: s.name for s in settings.sources}
    cat_lines = "\n".join("- %s：%s" % (c.id, c.name) for c in settings.categories)

    infos = []      # 与 groups 对应：(Candidate 降级值, 文章列表)
    for g in groups:
        arts = store.get_articles(list(g.article_ids))
        cat = _source_category(settings, arts[0].source_id) if arts else None
        if cat not in category_ids:
            cat = settings.categories[0].id
        text = "\n".join("%s\n%s" % (a.title, a.summary) for a in arts).lower()
        cand = Candidate(g, cat, False, FALLBACK_IMPORTANCE,
                         source_count=max(1, len({a.source_id for a in arts})),
                         followed=_hits(settings.keywords_follow, text),
                         blocked=_hits(settings.keywords_block, text))
        infos.append((cand, arts))

    result = [c for c, _ in infos]
    live = [k for k, g in enumerate(groups) if g.status != "stale"]   # 旧闻不会显示，不调用大模型
    for start in range(0, len(live), CLASSIFY_BATCH):
        batch = live[start:start + CLASSIFY_BATCH]
        user = "允许的类别：\n%s\n\n事件组：\n%s" % (
            cat_lines, "\n".join(_prompt_group(n, infos[k][1], names) for n, k in enumerate(batch, 1)))
        try:
            data = llm.chat_json(SYSTEM_PROMPT, user, max_tokens=2000)
        except LLMError:
            continue
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            continue
        seen = set()
        for item in items:
            v = _valid_item(item, len(batch), category_ids)
            if v is None or v[0] in seen:
                continue
            seen.add(v[0])
            k = batch[v[0] - 1]
            result[k] = replace(result[k], category=v[1], china_domestic=v[2], importance=v[3])
    return result


# ---------- select ----------

def _score(c: Candidate, weights: Dict[str, float]) -> float:
    bonus = min(SOURCE_BONUS_STEP * (c.source_count - 1), SOURCE_BONUS_CAP)
    return c.importance * weights.get(c.category, 1.0) + bonus + (FOLLOW_BONUS if c.followed else 0.0)


def _order(c: Candidate):
    first = c.linked.article_ids[0] if c.linked.article_ids else 0
    return (-c.score, -c.source_count, first)


def select(candidates: List[Candidate], settings: Settings) -> Selection:
    weights = {c.id: c.weight for c in settings.categories}
    hidden, kept, filtered = [], [], 0
    for c in candidates:
        if c.linked.status == "stale":
            hidden.append(c)
        elif (settings.filter_china_domestic and c.china_domestic) or c.blocked:
            filtered += 1
        else:
            kept.append(replace(c, score=_score(c, weights)))
    kept.sort(key=_order)
    top = kept[:settings.top_count]
    by_category = {}    # type: Dict[str, List[Candidate]]
    for c in kept[settings.top_count:]:
        rows = by_category.setdefault(c.category, [])
        if len(rows) < settings.per_category:
            rows.append(c)
    return Selection(top, by_category, hidden, filtered)
