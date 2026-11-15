"""build 编排与期号：采集 → 同日归并 → 跨天比对 → 分类排序 → 选出后合并 → 抓原文 → 摘要 → 入库 → 发布 → 清理。

退出码：0 正常；3 部分降级（一半以上来源失败、全部来源失败、超出大模型调用上限或大模型调用失败过多，
网页照常发布）；
1 其他失败。作业输出只有几行中文摘要；意外异常只报阶段名和异常类型，不带异常文字（可能含密钥或地址）。
"""
import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple

from . import cluster, extract, feeds, rank, render, summarize
from .llm import BudgetExceeded, LLMError
from .render import HiddenItem
from .settings import Settings
from .store import Article, Store, from_iso, to_iso
from .summarize import EventOut

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

FIRST_RUN_WINDOW = timedelta(hours=36)     # 没有成功 build 时的采集范围
ISSUE_CUTOFF_HOUR = 12                     # 本地时间 12:00 及以后的 build 属于次日那期
PRIORITY_TYPES = ("agency", "official")    # 抓原文时优先的来源类型


def issue_date_for(now_local: datetime) -> date:
    d = now_local.date()
    return d + timedelta(days=1) if now_local.hour >= ISSUE_CUTOFF_HOUR else d


@dataclass
class BuildResult:
    issue_date: date
    exit_code: int
    note: str
    lines: List[str] = field(default_factory=list)   # 作业输出（stdout）


class _TrackedLLM:
    """记录本次 build 的调用次数、失败次数（含 summarize 的），以及是否遇到 BudgetExceeded。

    各阶段会吞掉大模型错误并退回降级结果，所以失败只能在这里统计，由 run_build 决定是否告警。
    """

    def __init__(self, inner):
        self.inner = inner
        self.calls = 0
        self.failed = 0
        self.summarize_failed = 0
        self.budget_exhausted = False

    def chat_json(self, system, user, **kwargs):
        try:
            out = self.inner.chat_json(system, user, **kwargs)
        except BudgetExceeded:
            self.budget_exhausted = True
            raise
        except LLMError:
            self.calls += 1
            self.failed += 1
            if system.split("\n", 1)[0].strip() == "任务：summarize":
                self.summarize_failed += 1
            raise
        except Exception:
            self.calls += 1
            raise
        self.calls += 1
        return out

    def too_many_failures(self) -> bool:
        """有 summarize 调用失败，或一半及以上的调用失败。"""
        return self.summarize_failed > 0 or (self.calls > 0 and self.failed * 2 >= self.calls)


class _StageError(Exception):
    def __init__(self, stage, exc):
        super().__init__(stage)
        self.stage = stage
        self.exc = exc


# ---------- 各阶段的小步骤 ----------

def _window_start(store: Store, issue: str, now: datetime) -> Tuple[datetime, bool]:
    """本期文章范围的起点 (时间, 是否不含起点)：上一次成功发布其他期的 build 开始之后。

    同一期重试时仍包含此前为本期抓到的文章；上一期 build 自己抓到的文章（fetched_at 等于其开始时间）
    不再进入本期；失败的 build 抓到的文章留到下一次成功 build。从未成功过时取最近 36 小时。
    """
    r = store.conn.execute(
        "select started_at from builds where status in ('ok', 'degraded') and issue_date != ? "
        "order by started_at desc, id desc limit 1", (issue,)).fetchone()
    if r is None:
        return now - FIRST_RUN_WINDOW, False
    return from_iso(r[0]), True


def _build_articles(store: Store, issue: str, now: datetime) -> List[Article]:
    """本次 build 的文章：范围起点之后抓到的，去掉已属于其他期事件的。"""
    start, exclusive = _window_start(store, issue, now)
    other = {r[0] for r in store.conn.execute(
        "select ea.article_id from event_articles ea join events e on e.id = ea.event_id "
        "where e.issue_date != ?", (issue,))}
    return [a for a in store.articles_since(start)
            if a.id not in other and not (exclusive and a.fetched_at <= start)]


def _fetch_order(selection: rank.Selection, store: Store, settings: Settings) -> List[int]:
    """选出的事件按页面顺序（要闻在前，再按设置里的类别顺序）；每个事件只取摘要会用到的前几篇，
    其中 agency/official 来源在前；已有正文的不再抓。fetch_bodies 再截到 fetch_articles_max 篇。"""
    types = {s.id: s.type for s in settings.sources}

    def prio(a):
        return 0 if types.get(a.source_id) in PRIORITY_TYPES else 1

    placed = list(selection.top)
    for cat in settings.categories:
        placed.extend(selection.by_category.get(cat.id, []))
    out = []    # type: List[int]
    for c in placed:
        arts = store.get_articles(list(c.linked.article_ids[:summarize.MAX_ARTICLES]))
        out.extend(a.id for a in sorted(arts, key=lambda a: (prio(a), a.id))
                   if a.body_status != "ok" and a.id not in out)
    return out


def _hidden(selection: rank.Selection, store: Store) -> List[HiddenItem]:
    out = []
    for c in selection.hidden_stale:
        prev = c.linked.prev_event_id
        title = None
        if prev is not None:
            r = store.conn.execute("select title_zh from events where id = ?", (prev,)).fetchone()
            title = r[0] if r and r[0] else None
        if title is None:
            arts = store.get_articles(list(c.linked.article_ids[:1]))
            title = arts[0].title if arts else ""
        out.append(HiddenItem(title, store.event_issue_date(prev) if prev is not None else None))
    return out


def _persist(store: Store, issue: str, events: List[EventOut], hidden_count: int, status: str,
             now: datetime) -> None:
    """一个事务内：删掉本期旧事件，写入新事件及其文章关联，更新 issues 行。"""
    with store.conn:
        c = store.conn
        c.execute("delete from event_articles where event_id in (select id from events where issue_date = ?)",
                  (issue,))
        c.execute("delete from events where issue_date = ?", (issue,))
        for ev in events:
            first_issue = issue
            if ev.prev_event_id is not None:
                r = c.execute("select first_issue, issue_date from events where id = ?",
                              (ev.prev_event_id,)).fetchone()
                if r:
                    first_issue = r[0] or r[1]
            cur = c.execute(
                "insert into events(issue_date, title_zh, title_orig, category, status, first_issue, "
                "prev_event_id, score, summary_zh, detail_zh, change_zh, degraded, basis, section) "
                "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (issue, ev.title_zh, ev.title_orig, ev.category, ev.status, first_issue, ev.prev_event_id,
                 ev.score, ev.summary_zh, ev.detail_zh, ev.change_zh, 1 if ev.degraded else 0,
                 ev.basis, ev.section))
            for aid in ev.article_ids:
                c.execute("insert or ignore into event_articles(event_id, article_id) values (?, ?)",
                          (cur.lastrowid, aid))
        c.execute("insert into issues(date, built_at, item_count, hidden_count, status) values (?, ?, ?, ?, ?) "
                  "on conflict(date) do update set built_at = excluded.built_at, "
                  "item_count = excluded.item_count, hidden_count = excluded.hidden_count, "
                  "status = excluded.status",
                  (issue, to_iso(now), len(events), hidden_count, status))


def _set_issue_status(store: Store, issue: str, status: str) -> None:
    with store.conn:
        store.conn.execute("update issues set status = ? where date = ?", (status, issue))


def _stored_events(store: Store, issue: str, settings: Settings) -> List[EventOut]:
    """从 events / event_articles / articles 还原某期的 EventOut（顺序与当时写入一致）。"""
    names = {s.id: s.name for s in settings.sources}
    rows = store.conn.execute(
        "select id, title_zh, title_orig, category, status, summary_zh, detail_zh, change_zh, "
        "degraded, basis, section, score, prev_event_id from events where issue_date = ? order by id",
        (issue,)).fetchall()
    out = []
    for r in rows:
        ids = tuple(x[0] for x in store.conn.execute(
            "select article_id from event_articles where event_id = ? order by article_id", (r[0],)))
        arts = store.get_articles(list(ids))
        prev = r[12]
        out.append(EventOut(
            title_zh=r[1], title_orig=r[2] or "", category=r[3] or "", status=r[4] or "new",
            summary_zh=r[5] or "", detail_zh=r[6] or "", change_zh=r[7],
            sources=summarize._sources(arts, names),
            prev_issue=store.event_issue_date(prev) if prev is not None else None,
            degraded=bool(r[8]), basis=r[9] or "snippet", section=r[10] or "category",
            score=r[11] or 0.0, article_ids=ids, prev_event_id=prev))
    return out


def _stored_hidden(site_dir: str, issue: str) -> List[HiddenItem]:
    try:
        with open(os.path.join(site_dir, issue, "issue.json"), encoding="utf-8") as f:
            raw = json.load(f).get("hidden")
    except (OSError, ValueError, AttributeError):
        return []
    out = []
    for h in raw if isinstance(raw, list) else []:
        if isinstance(h, dict) and isinstance(h.get("title_zh"), str):
            prev = h.get("prev_issue")
            out.append(HiddenItem(h["title_zh"], prev if isinstance(prev, str) else None))
    return out


def _previous_published(store: Store, issue: str) -> Optional[str]:
    for r in store.list_issues():
        if r.date < issue and r.status in render.PUBLISHED:
            return r.date
    return None


def _failed_line(report: feeds.CollectReport) -> str:
    if not report.failed:
        return "来源失败：无"
    return "来源失败：" + "；".join("%s（%s）" % (sid, msg) for sid, msg in report.failed.items())


def _sources_degraded(settings: Settings, report: feeds.CollectReport) -> bool:
    enabled = sum(1 for s in settings.sources if s.enabled)
    failed = len(report.failed)
    return enabled == 0 or failed / enabled > 0.5     # 含全部失败


# ---------- 编排 ----------

_CURRENT_STAGE = "准备"


def current_stage() -> str:
    """The stage the running build is in, for a termination message."""
    return _CURRENT_STAGE


def run_build(settings: Settings, store: Store, llm, now: datetime, progress=None) -> BuildResult:
    """progress, if given, receives 「开始：<阶段>」 each time a new stage starts."""
    tz = ZoneInfo(settings.timezone)
    issue_date = issue_date_for(now.astimezone(tz))
    issue = issue_date.isoformat()
    last = store.last_successful_build()
    # 采集范围至少覆盖最近 36 小时：来源恢复后，它停机期间发布的条目仍能补采（URL 唯一，不会重复入库）
    since = now - FIRST_RUN_WINDOW
    if last is not None and last.started_at < since:
        since = last.started_at
    build_id = store.start_build(now, issue)
    tracked = _TrackedLLM(llm)
    global _CURRENT_STAGE
    _CURRENT_STAGE = "准备"

    def enter(name):
        global _CURRENT_STAGE
        if name != _CURRENT_STAGE:
            _CURRENT_STAGE = name
            if progress is not None and name != "完成":
                progress("开始：" + name)

    def stage(name, fn, *args):
        enter(name)
        try:
            return fn(*args)
        except Exception as e:
            raise _StageError(name, e)

    try:
        report = stage("采集", lambda: feeds.collect(settings, store, since, now))
        articles = stage("归并", _build_articles, store, issue, now)
        groups = stage("归并", cluster.group_same_day, articles, tracked)
        linked = stage("跨天比对", cluster.link_previous, groups, store, issue_date,
                       settings.lookback_days, tracked)
        candidates = stage("分类", rank.classify, linked, store, settings, tracked)
        selection = stage("分类", rank.select, candidates, settings)
        selection = stage("合并", rank.merge_placed, selection, candidates, store, settings, tracked)
        stage("抓取原文", lambda: extract.fetch_bodies(store, settings, _fetch_order(selection, store, settings)))
        events = stage("摘要", summarize.summarize, selection, store, tracked, settings)
        hidden = stage("摘要", _hidden, selection, store)

        reasons = []
        if _sources_degraded(settings, report):
            reasons.append("一半以上来源失败")
        if tracked.budget_exhausted:
            reasons.append("超出大模型调用上限")
        if tracked.too_many_failures():
            reasons.append("大模型调用失败 %d 次" % tracked.failed)
        status = "degraded" if reasons else "ok"
        # 先以 failed 入库：页面生成成功前中断，期不会被当成已发布
        stage("保存", _persist, store, issue, events, len(hidden), "failed", now)
        enter("发布")
        try:
            render.render_issue(issue_date, events, hidden, settings, settings.site_dir)
            # render_index 只列已发布的期，所以先改状态再生成首页；失败时改回 failed
            _set_issue_status(store, issue, status)
            render.render_index(store, settings.site_dir)
        except Exception as e:
            _set_issue_status(store, issue, "failed")
            raise _StageError("发布", e)
        warnings = []
        try:
            prev = _previous_published(store, issue)
            if prev is not None:
                render.render_issue(date.fromisoformat(prev), _stored_events(store, prev, settings),
                                    _stored_hidden(settings.site_dir, prev), settings, settings.site_dir)
        except Exception as e:  # 上一期只是补「下一期」链接，失败不影响本期
            warnings.append("上一期页面更新失败：%s" % type(e).__name__)
        stage("清理", store.prune, now)
        enter("完成")
    except (SystemExit, KeyboardInterrupt):
        # Stopped (e.g. the Agent's timeout sent SIGTERM): close the builds row, then stop.
        _finish(store, build_id, "failed", "构建被终止（阶段：%s）" % _CURRENT_STAGE)
        raise
    except _StageError as e:
        msg = "构建失败（阶段：%s）：%s" % (e.stage, type(e.exc).__name__)
        _finish(store, build_id, "failed", msg)
        return BuildResult(issue_date, 1, msg, [msg])

    followups = sum(1 for ev in events if ev.status == "followup")
    lines = [
        "期号 %s" % issue,
        "新文章 %d" % report.new_articles,
        "事件 %d（后续 %d，隐藏旧闻 %d，过滤国内 %d）" % (len(events), followups, len(hidden), selection.filtered),
        _failed_line(report),
        "大模型调用 %d 次" % tracked.calls,
    ]
    if tracked.budget_exhausted:
        lines.append("已超出今日大模型调用上限，部分条目未生成摘要")
    if tracked.too_many_failures():
        lines.append("大模型调用失败 %d 次" % tracked.failed)
    lines.extend(warnings)
    problems = reasons + warnings
    note = "期号 %s，事件 %d" % (issue, len(events)) + ("；降级：" + "、".join(problems) if problems else "")
    _finish(store, build_id, "degraded" if problems else "ok", note)
    return BuildResult(issue_date, 3 if problems else 0, note, lines)


def _finish(store: Store, build_id: int, status: str, note: str) -> None:
    try:
        store.finish_build(build_id, datetime.now().astimezone(), status, note)
    except Exception:  # 记录 builds 失败不改变本次结果
        pass
