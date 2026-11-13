"""同日归并（把同一事件的多篇报道聚成一组）与跨天比对（新事件 / 后续 / 无进展旧闻）。

两步都是“便宜的初筛 + 大模型确认”。大模型失败、超出调用上限或输出不合格时退回初筛结果，
这里的函数不向外抛出大模型错误。
"""
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from difflib import SequenceMatcher
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit

from .llm import BudgetExceeded, LLMError
from .store import Article, EventRow, Store

GROUP_SIMILARITY = 0.6        # 同日初分组的标题相似度阈值
CLUSTER_BATCH = 40            # 每次 cluster 调用最多的标题数
SUMMARY_CHARS = 200           # 提示里每条简介最多的字符数
CANDIDATE_SCORE = 0.2         # 往期候选事件的得分下限：max(标题相似度, 实词重合度)；只决定给大模型看什么，偏向召回
MAX_CANDIDATES = 5            # 每组最多给大模型的往期候选数
STALE_FALLBACK = 0.8          # 大模型不可用时，相似度达到此值记为旧闻
LINK_GROUPS_PER_CALL = 8      # 每次 link 调用判定的组数


@dataclass(frozen=True)
class Linked:
    article_ids: Tuple[int, ...]
    status: str                    # "new" | "followup" | "stale"
    prev_event_id: Optional[int]


# ---------- 文本规范化与相似度 ----------

# 结尾的来源名，例如 " - BBC News"、" | The Guardian"
_SUFFIX = re.compile(r"\s+[-|–—]\s+[^-|–—]{1,40}$")
_TRACKING = re.compile(r"^(utm_.*|fbclid|gclid|ocid|cmpid)$", re.I)


def normalize_title(title: str) -> str:
    t = (title or "").strip()
    m = _SUFFIX.search(t)
    if m and len(t[:m.start()].strip()) >= 12 and len(m.group(0).split()) <= 6:
        t = t[:m.start()]
    t = t.lower()
    t = "".join(" " if unicodedata.category(c)[0] in "PS" else c for c in t)
    return " ".join(t.split())


# 中日韩文字没有空格分词，按单字作为词元；其他文字按空格分词
_CJK = "\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af"
_TOKEN = re.compile("[%s]|[^\\s%s]+" % (_CJK, _CJK))


def title_tokens(title: str) -> Tuple[str, ...]:
    return tuple(_TOKEN.findall(normalize_title(title)))


def title_similarity(a: str, b: str) -> float:
    """规范化标题按词元序列计算的 SequenceMatcher 相似度（0–1）。"""
    return _ratio(title_tokens(a), title_tokens(b))


def _ratio(ta: Tuple[str, ...], tb: Tuple[str, ...], floor: float = 0.0) -> float:
    """词元序列的相似度；低于 floor 时可能返回 0（只用于与阈值比较）。"""
    if not ta or not tb:
        return 0.0
    if ta == tb:
        return 1.0
    m = SequenceMatcher(None, ta, tb, autojunk=False)
    if floor and (m.real_quick_ratio() < floor or m.quick_ratio() < floor):
        return 0.0
    return m.ratio()


def normalize_url(url: str) -> str:
    try:
        p = urlsplit((url or "").strip())
    except ValueError:
        return (url or "").strip()
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    query = urlencode([(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                       if not _TRACKING.match(k)])
    key = host + (p.path.rstrip("/") or "")
    return key + ("?" + query if query else "")


# ---------- 同日归并 ----------

_CLUSTER_SYSTEM = """任务：cluster
你会看到同一天的一批新闻条目，每条以 [编号] 开头，附标题，部分附简介。
请把报道同一个具体事件的条目分到同一组。
规则：
- 只根据给出的文字判断，不要凭外部知识推测。
- “同一事件”指同一件具体的事（同一次发布、同一项决定、同一场会议、同一起事故等）；只是话题相近（例如都关于 AI、都关于选举）不算同一事件。
- 每个编号必须出现且只出现一次；与其他条目都不是同一事件的条目单独成组。
只输出 JSON，格式：{"groups": [[编号, 编号, ...], [编号], ...]}"""


class _UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def _pre_group(articles: Sequence[Article]) -> List[List[int]]:
    """按规范化标题相似度与相同链接做传递式初分组；返回输入位置的列表，按首次出现排序。"""
    n = len(articles)
    titles = [title_tokens(a.title) for a in articles]
    urls = [normalize_url(a.url) for a in articles]
    uf = _UnionFind(n)
    for j in range(n):
        for i in range(j):
            if urls[i] and urls[i] == urls[j]:
                uf.union(i, j)
            elif _ratio(titles[i], titles[j], GROUP_SIMILARITY) >= GROUP_SIMILARITY:
                uf.union(i, j)
    groups = {}  # type: Dict[int, List[int]]
    for i in range(n):
        groups.setdefault(uf.find(i), []).append(i)
    return sorted(groups.values(), key=lambda g: g[0])


def _batches(pre: List[List[int]]) -> List[List[List[int]]]:
    """按顺序把初分组装批，每批不超过 CLUSTER_BATCH 条，初分组不拆开。"""
    out = []  # type: List[List[List[int]]]
    cur = []  # type: List[List[int]]
    size = 0
    for g in pre:
        if cur and size + len(g) > CLUSTER_BATCH:
            out.append(cur)
            cur, size = [], 0
        cur.append(g)
        size += len(g)
    if cur:
        out.append(cur)
    return out


def _cluster_prompt(articles: Sequence[Article], positions: List[int]) -> str:
    lines = []
    for k, pos in enumerate(positions, 1):
        a = articles[pos]
        line = "[%d] %s" % (k, " ".join((a.title or "").split()))
        summary = " ".join((a.summary or "").split())[:SUMMARY_CHARS]
        if summary:
            line += "\n    简介：" + summary
        lines.append(line)
    return "\n".join(lines)


def _valid_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _parse_groups(out, n: int) -> Optional[List[List[int]]]:
    """校验 {"groups": [[1-based 编号...]...]}：每个编号恰好出现一次。不合格返回 None。"""
    groups = out.get("groups") if isinstance(out, dict) else None
    if not isinstance(groups, list):
        return None
    seen = set()
    result = []
    for g in groups:
        if not isinstance(g, list) or not g:
            return None
        for x in g:
            if not _valid_int(x) or not 1 <= x <= n or x in seen:
                return None
            seen.add(x)
        result.append([x - 1 for x in g])
    if len(seen) != n:
        return None
    return result


def group_same_day(articles: List[Article], llm) -> List[List[int]]:
    """把同一天的文章按事件分组，返回文章 id 的分组（组内与组间都按输入顺序）。"""
    pre = _pre_group(articles)
    final = []  # type: List[List[int]]  输入位置
    llm_ok = True
    for batch in _batches(pre):
        positions = [p for g in batch for p in g]
        decided = None
        if llm_ok and 1 < len(positions) <= CLUSTER_BATCH:
            try:
                out = llm.chat_json(_CLUSTER_SYSTEM, _cluster_prompt(articles, positions))
                parsed = _parse_groups(out, len(positions))
                if parsed is not None:
                    decided = [[positions[k] for k in g] for g in parsed]
            except BudgetExceeded:
                llm_ok = False
            except LLMError:
                pass
        final.extend(decided if decided is not None else batch)
    final = [sorted(g) for g in final]
    final.sort(key=lambda g: g[0])
    return [[articles[p].id for p in g] for g in final]


# ---------- 跨天比对 ----------

_LINK_SYSTEM = """任务：link
你会看到今天的若干组新闻（每组是同一事件的几篇报道），以及每组可能相关的往期事件（带事件编号、期号、标题和摘要）。
请对每组判断它与所列往期事件的关系：
- "stale"：与某个往期事件是同一事件，并且没有实质性新进展（只是重复报道、换种说法、补充背景或细枝末节）。
- "followup"：与某个往期事件属于同一个持续发展的事件，并且有实质性新进展（例如新的决定、结果、数据、表态或行动）。
- "new"：与所列往期事件都不是同一事件。
规则：
- 只根据给出的文字判断，不要凭外部知识推测。
- 选 "stale" 或 "followup" 时，event_id 必须是该组所列候选中的事件编号；选 "new" 时 event_id 为 null。
- 每组给出一项结果。
只输出 JSON，格式：{"results": [{"group": 组号, "decision": "stale" | "followup" | "new", "event_id": 事件编号或 null}, ...]}"""


# 计算实词重合度时去掉的英文虚词；中日韩文字仍按单字计
_STOPWORDS = frozenset("the a an of in on to for and at by with from as is are was be after over its new says say"
                       .split())

_TitleKey = Tuple[Tuple[str, ...], FrozenSet[str]]
_Candidate = Tuple[float, float, EventRow]  # (候选得分, 标题相似度, 事件)


def _title_key(tokens: Tuple[str, ...]) -> _TitleKey:
    return tokens, frozenset(t for t in tokens if t not in _STOPWORDS)


def _overlap(a: FrozenSet[str], b: FrozenSet[str]) -> float:
    """共有实词数 / 较短标题的实词数。"""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _event_titles(e: EventRow) -> List[_TitleKey]:
    return [_title_key(tt) for tt in (title_tokens(t) for t in (e.title_orig, e.title_zh) if t) if tt]


def _candidates(titles: List[_TitleKey], events: List[EventRow],
                event_titles: Dict[int, List[_TitleKey]]) -> List[_Candidate]:
    scored = []
    for order, e in enumerate(events):
        best_score = best_sim = 0.0
        for tokens, content in titles:
            for etokens, econtent in event_titles[e.id]:
                sim = _ratio(tokens, etokens, CANDIDATE_SCORE)
                best_sim = max(best_sim, sim)
                best_score = max(best_score, sim, _overlap(content, econtent))
        if best_score >= CANDIDATE_SCORE:
            scored.append((best_score, best_sim, order, e))
    # 得分高者在前；同分时较新的事件在前（events 已按新到旧排序）
    scored.sort(key=lambda x: (-x[0], x[2]))
    return [(score, sim, e) for score, sim, _, e in scored[:MAX_CANDIDATES]]


def _fallback(ids: Tuple[int, ...], cands: List[_Candidate]) -> Linked:
    """大模型不可用：只按标题相似度判断，最相似的候选达到 STALE_FALLBACK 记为旧闻。"""
    if cands:
        sim, e = max(((c[1], c[2]) for c in cands), key=lambda x: x[0])
        if sim >= STALE_FALLBACK:
            return Linked(ids, "stale", e.id)
    return Linked(ids, "new", None)


def _clip(text: Optional[str]) -> str:
    return " ".join((text or "").split())[:SUMMARY_CHARS]


def _link_prompt(chunk, by_id: Dict[int, Article]) -> str:
    parts = []
    for k, (ids, cands) in enumerate(chunk, 1):
        lines = ["第 %d 组" % k, "今天的报道："]
        for i in ids:
            a = by_id[i]
            line = "- " + " ".join((a.title or "").split())
            if _clip(a.summary):
                line += "\n  简介：" + _clip(a.summary)
            lines.append(line)
        lines.append("候选往期事件：")
        for _, _, e in cands:
            title = " / ".join(t for t in (e.title_zh, e.title_orig) if t)
            line = "- 事件编号 %d（期号 %s）：%s" % (e.id, e.issue_date.isoformat(), " ".join(title.split()))
            if _clip(e.summary_zh):
                line += "\n  摘要：" + _clip(e.summary_zh)
            lines.append(line)
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _parse_link(out, chunk) -> Dict[int, Linked]:
    """校验 link 输出，返回 {组下标: Linked}；不合格的项不出现在结果中。"""
    results = out.get("results") if isinstance(out, dict) else None
    if not isinstance(results, list):
        return {}
    decided = {}  # type: Dict[int, Linked]
    for item in results:
        if not isinstance(item, dict):
            continue
        g, decision, event_id = item.get("group"), item.get("decision"), item.get("event_id")
        if not _valid_int(g) or not 1 <= g <= len(chunk) or (g - 1) in decided:
            continue
        ids, cands = chunk[g - 1]
        if decision == "new":
            decided[g - 1] = Linked(ids, "new", None)
        elif decision in ("stale", "followup"):
            if _valid_int(event_id) and event_id in {e.id for _, _, e in cands}:
                decided[g - 1] = Linked(ids, decision, event_id)
    return decided


def link_previous(groups: List[List[int]], store: Store, issue_date: date, lookback_days: int,
                  llm) -> List[Linked]:
    """把今天的每组与最近 lookback_days 天（期号早于 issue_date）的事件比对。返回与 groups 同序的 Linked。"""
    events = store.recent_events(before_issue=issue_date,
                                 since_issue=issue_date - timedelta(days=lookback_days))
    event_titles = {e.id: _event_titles(e) for e in events}
    by_id = {a.id: a for a in store.get_articles([i for g in groups for i in g])}

    results = [None] * len(groups)  # type: List[Optional[Linked]]
    pending = []  # (下标, ids, 候选)
    for idx, g in enumerate(groups):
        ids = tuple(g)
        titles = [title_tokens(by_id[i].title) for i in ids if i in by_id]
        cands = _candidates([_title_key(t) for t in titles if t], events, event_titles)
        if not cands:
            results[idx] = Linked(ids, "new", None)
        else:
            pending.append((idx, ids, cands))

    llm_ok = True
    for start in range(0, len(pending), LINK_GROUPS_PER_CALL):
        part = pending[start:start + LINK_GROUPS_PER_CALL]
        chunk = [(ids, cands) for _, ids, cands in part]
        decided = {}  # type: Dict[int, Linked]
        if llm_ok:
            # 送给大模型的只有存在于库中的文章；ids 原样保留在结果里
            prompt_chunk = [(tuple(i for i in ids if i in by_id), cands) for ids, cands in chunk]
            try:
                out = llm.chat_json(_LINK_SYSTEM, _link_prompt(prompt_chunk, by_id))
                decided = _parse_link(out, chunk)
            except BudgetExceeded:
                llm_ok = False
            except LLMError:
                pass
        for k, (idx, ids, cands) in enumerate(part):
            results[idx] = decided.get(k) or _fallback(ids, cands)
    return [r for r in results if r is not None]
