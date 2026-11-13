"""SQLite 存储。时间一律存为 UTC 的 ISO-8601 字符串（YYYY-MM-DDTHH:MM:SSZ），可直接按字典序比较。"""
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

ARTICLE_RETENTION_DAYS = 30

SCHEMA = """
create table if not exists articles(
  id integer primary key autoincrement,
  source_id text not null,
  url text not null unique,
  title text not null,
  summary text not null default '',
  published_at text,
  fetched_at text not null,
  body_text text,
  body_status text
);
create index if not exists articles_fetched on articles(fetched_at);
create table if not exists events(
  id integer primary key autoincrement,
  issue_date text not null,
  title_zh text not null,
  title_orig text,
  category text,
  status text,
  first_issue text,
  prev_event_id integer,
  score real,
  summary_zh text,
  detail_zh text,
  change_zh text,
  degraded integer not null default 0,
  basis text,
  section text
);
create index if not exists events_issue on events(issue_date);
create table if not exists event_articles(
  event_id integer not null,
  article_id integer not null,
  primary key(event_id, article_id)
);
create table if not exists issues(
  date text primary key,
  built_at text,
  item_count integer,
  hidden_count integer,
  status text
);
create table if not exists builds(
  id integer primary key autoincrement,
  started_at text not null,
  ended_at text,
  issue_date text,
  status text,
  note text
);
create table if not exists llm_calls(
  day text primary key,
  count integer not null default 0
);
"""


def to_iso(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def from_iso(s):
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class Article:
    id: int
    source_id: str
    url: str
    title: str
    summary: str
    published_at: Optional[datetime]
    fetched_at: datetime
    body_text: Optional[str]
    body_status: Optional[str]


@dataclass(frozen=True)
class EventRow:
    """跨天比对用的往期事件。"""
    id: int
    issue_date: date
    title_zh: str
    title_orig: Optional[str]
    summary_zh: Optional[str]


@dataclass(frozen=True)
class Build:
    id: int
    started_at: datetime
    ended_at: Optional[datetime]
    issue_date: Optional[str]
    status: Optional[str]
    note: Optional[str]


@dataclass(frozen=True)
class IssueRow:
    date: str            # YYYY-MM-DD
    item_count: int
    status: Optional[str]


class Store:
    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    def add_articles(self, items, fetched_at=None) -> List[int]:
        """新增文章并返回新行的 id；URL 已存在的跳过。"""
        stamp = to_iso(fetched_at or datetime.now(timezone.utc))
        ids = []
        with self.conn:
            for it in items:
                cur = self.conn.execute(
                    "insert or ignore into articles(source_id, url, title, summary, published_at, fetched_at) "
                    "values (?, ?, ?, ?, ?, ?)",
                    (it.source_id, it.url, it.title, it.summary,
                     to_iso(it.published_at) if it.published_at else None, stamp))
                if cur.rowcount:
                    ids.append(cur.lastrowid)
        return ids

    def get_articles(self, ids) -> List[Article]:
        """按给定 id 的顺序返回文章；不存在的 id 跳过。"""
        found = {}
        for i in ids:
            r = self.conn.execute(
                "select id, source_id, url, title, summary, published_at, fetched_at, body_text, body_status "
                "from articles where id = ?", (i,)).fetchone()
            if r is not None:
                found[i] = Article(r[0], r[1], r[2], r[3], r[4], from_iso(r[5]), from_iso(r[6]), r[7], r[8])
        return [found[i] for i in ids if i in found]

    def set_body(self, article_id, body_text, body_status):
        with self.conn:
            self.conn.execute("update articles set body_text=?, body_status=? where id=?",
                              (body_text, body_status, article_id))

    def articles_since(self, t) -> List[Article]:
        """抓取时间不早于 t 的文章，按 id 升序。"""
        rows = self.conn.execute(
            "select id, source_id, url, title, summary, published_at, fetched_at, body_text, body_status "
            "from articles where fetched_at >= ? order by id", (to_iso(t),)).fetchall()
        return [Article(r[0], r[1], r[2], r[3], r[4], from_iso(r[5]), from_iso(r[6]), r[7], r[8])
                for r in rows]

    def recent_events(self, before_issue: date, since_issue: date) -> List[EventRow]:
        """期号在 [since_issue, before_issue) 内的事件，新期在前、同期按 id 倒序。当期事件不包括在内。"""
        rows = self.conn.execute(
            "select id, issue_date, title_zh, title_orig, summary_zh from events "
            "where issue_date >= ? and issue_date < ? order by issue_date desc, id desc",
            (since_issue.isoformat(), before_issue.isoformat())).fetchall()
        return [EventRow(r[0], date.fromisoformat(r[1]), r[2], r[3], r[4]) for r in rows]

    def event_issue_date(self, event_id) -> Optional[str]:
        """事件所在期号（YYYY-MM-DD）；事件不存在时为 None。"""
        r = self.conn.execute("select issue_date from events where id = ?", (event_id,)).fetchone()
        return r[0] if r else None

    def start_build(self, started_at, issue_date=None) -> int:
        with self.conn:
            cur = self.conn.execute("insert into builds(started_at, issue_date) values (?, ?)",
                                    (to_iso(started_at), issue_date))
        return cur.lastrowid

    def finish_build(self, build_id, ended_at, status, note=""):
        with self.conn:
            self.conn.execute("update builds set ended_at=?, status=?, note=? where id=?",
                              (to_iso(ended_at), status, note, build_id))

    def last_successful_build(self) -> Optional[Build]:
        """最近一次状态为 ok 或 degraded（即已发布）的 build；按开始时间取最新。"""
        r = self.conn.execute(
            "select id, started_at, ended_at, issue_date, status, note from builds "
            "where status in ('ok', 'degraded') order by started_at desc, id desc limit 1").fetchone()
        if r is None:
            return None
        return Build(r[0], from_iso(r[1]), from_iso(r[2]), r[3], r[4], r[5])

    def list_issues(self) -> List[IssueRow]:
        """所有期（含失败的），新期在前。"""
        rows = self.conn.execute(
            "select date, item_count, status from issues order by date desc").fetchall()
        return [IssueRow(r[0], r[1] or 0, r[2]) for r in rows]

    def llm_calls_today(self, day) -> int:
        r = self.conn.execute("select count from llm_calls where day = ?", (day.isoformat(),)).fetchone()
        return r[0] if r else 0

    def count_llm_call(self, day) -> None:
        with self.conn:
            self.conn.execute("insert into llm_calls(day, count) values(?, 1) "
                              "on conflict(day) do update set count = count + 1", (day.isoformat(),))

    def prune(self, now) -> int:
        """删除抓取时间早于 30 天的文章及其 event_articles 关联；事件本身保留。返回删除的文章数。"""
        cutoff = to_iso(now - timedelta(days=ARTICLE_RETENTION_DAYS))
        with self.conn:
            self.conn.execute("delete from event_articles where article_id in "
                              "(select id from articles where fetched_at < ?)", (cutoff,))
            cur = self.conn.execute("delete from articles where fetched_at < ?", (cutoff,))
        return cur.rowcount
