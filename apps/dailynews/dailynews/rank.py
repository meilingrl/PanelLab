"""分类、过滤与排序：给每个事件组定类别、是否中国国内事务、重要性，再按分数选出要闻和各类别条目；
选出后再用一次大模型调用合并被分在不同批里的同一事件（merge_placed）。

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


# ---------- merge ----------

MERGE_SNIPPET_CHARS = 120      # merge 提示里每条简介最多的字符数

MERGE_SYSTEM = """任务：merge
你会看到今天简报里已选出的事件，每条以 [编号] 开头，附标题，部分附简介开头。
请找出报道同一个具体事件的条目（同一次发布、同一项决定、同一场会议、同一起事故等）；只是话题相近不算。
规则：
- 只根据给出的文字判断，不要凭外部知识推测。
- 每组至少两个编号，每个编号最多出现一次；不与其他条目重复的条目不要列出；没有重复时输出空列表。
只输出 JSON：{"merge": [[编号, 编号, ...], ...]}"""


def _placed(selection: Selection, settings: Settings) -> List[Candidate]:
    out = list(selection.top)
    for cat in settings.categories:
        out.extend(selection.by_category.get(cat.id, []))
    return out


def _parse_merge(out, n: int) -> Optional[List[List[int]]]:
    """校验 {"merge": [[1-based 编号...]...]}：编号在范围内、全局最多出现一次、每组至少两个。不合格返回 None。"""
    groups = out.get("merge") if isinstance(out, dict) else None
    if not isinstance(groups, list):
        return None
    seen = set()
    result = []
    for g in groups:
        if not isinstance(g, list) or len(g) < 2:
            return None
        for x in g:
            if isinstance(x, bool) or not isinstance(x, int) or not 1 <= x <= n or x in seen:
                return None
            seen.add(x)
        result.append([x - 1 for x in g])
    return result


def merge_placed(selection: Selection, candidates: List[Candidate], store, settings: Settings,
                 llm) -> Selection:
    """把已选出的事件里报道同一件事的合并成一个，再重新选（空出的位置由其余候选补上）。

    合并后的事件沿用得分最高成员的类别、状态与往期事件，文章合并去重，来源数重新计算。
    大模型失败或输出不合格时原样返回。
    """
    placed = _placed(selection, settings)
    if len(placed) < 2:
        return selection
    lines = []
    for n, c in enumerate(placed, 1):
        arts = store.get_articles(list(c.linked.article_ids[:1]))
        title = " ".join(arts[0].title.split()) if arts else ""
        line = "[%d] %s" % (n, title)
        snippet = " ".join((arts[0].summary or "").split())[:MERGE_SNIPPET_CHARS] if arts else ""
        if snippet:
            line += "\n    简介：" + snippet
        lines.append(line)
    try:
        groups = _parse_merge(llm.chat_json(MERGE_SYSTEM, "\n".join(lines)), len(placed))
    except LLMError:
        return selection
    if not groups:
        return selection

    by_key = {c.linked.article_ids: c for c in candidates}     # 未打分的原候选
    replace_at = {}     # 原候选的 article_ids -> 合并后的候选（None 表示被并入别处）
    for g in groups:
        members = [placed[k] for k in g]
        best = max(members, key=lambda c: c.score)              # 同分取页面上靠前的
        ids = []
        for c in [best] + [m for m in members if m is not best]:
            ids.extend(i for i in c.linked.article_ids if i not in ids)
        arts = store.get_articles(ids)
        merged = replace(by_key.get(best.linked.article_ids, best),
                         linked=Linked(tuple(ids), best.linked.status, best.linked.prev_event_id),
                         source_count=max(1, len({a.source_id for a in arts})),
                         followed=any(m.followed for m in members), score=0.0)
        for m in members:
            replace_at[m.linked.article_ids] = merged if m is best else None
    new = []
    for c in candidates:
        if c.linked.article_ids not in replace_at:
            new.append(c)
        elif replace_at[c.linked.article_ids] is not None:
            new.append(replace_at[c.linked.article_ids])
    return select(new, settings)
