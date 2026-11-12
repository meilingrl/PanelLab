# 新版 DailyNews 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal：** 一个只用标准库的 Python 程序 `dailynews`，每天采集权威国际来源、跨天去重、用 DeepSeek 写中文摘要，生成带密码保护的静态网页简报（`news.panellab.cn`），并发一封提醒邮件；作为两个普通的服务器 Agent 作业运行。

**Architecture：**
- 新目录 `apps/dailynews/`，包 `dailynews`，不导入 `backend/` 或 `agent/`。
- 流水线各阶段结果落 SQLite（`/var/lib/dailynews/dailynews.db`），`build` 依次跑：采集 → 同日归并 → 跨天比对 → 抽正文（只对非旧闻）→ 分类/过滤/排序 → 摘要 → 生成网页；`notify` 单独发邮件。
- 网页是静态文件，筛选、主题、展开在浏览器端完成；nginx + Let's Encrypt + 基本认证由用户按文档手工配置。

**Tech Stack：** Python ≥ 3.9 标准库（urllib、xml.etree、html.parser、sqlite3、smtplib、zoneinfo、json、difflib）；pytest；原生 HTML/CSS/JS。

**Spec：** [design-dailynews.md](design-dailynews.md)（另参考 [design-server-agent.md](design-server-agent.md) 的作业模型）

## Global Constraints

- 只用 Python 标准库，语法与 API 兼容 Python 3.9（不用 `match`，运行时不用 `X | Y`）。CI 用 3.9 跑测试。
- 不在 `backend/`、`agent/`、`frontend/` 中加任何新闻专用代码。
- 路径：程序 `/srv/dailynews/app/`（属主 root，panellab 可读）；设置 `/srv/dailynews/settings.json`（属主 panellab，0640）；数据 `/var/lib/dailynews/`（属主 panellab，0750）；网站 `/srv/dailynews/site/`（属主 panellab，目录 0755，文件 0644，nginx 可读）。
- 以 `panellab` 用户运行（Agent 作业）。秘密（`DAILYNEWS_LLM_API_KEY`、`DAILYNEWS_SMTP_PASSWORD`）只来自环境变量（Agent 的秘密环境变量），不写进设置文件、日志、网页或错误信息。
- 大模型：OpenAI 兼容 Chat Completions，默认 `base_url` `https://api.deepseek.com`，模型 `deepseek-chat`。
- 每天调用上限默认 2,000 次；超出后剩余条目退回原文片段并标注「未生成摘要」，作业输出记录，且 `build` 以退出码 3 结束让 Agent 告警（网页仍然发布）。
- 简报全部中文；外文标题给中文译文，原标题在展开内容里。主要关于中国国内事务的条目默认过滤，涉及中国的国际新闻保留。
- 只采集设置文件来源清单里的来源；任何链接都不从聚合站或搜索结果引入。
- 期号 = 提醒当天的日期：时区 `Asia/Shanghai`（设置项），`build` 在本地时间 12:00 及以后启动时期号为次日，否则为当天。采集范围是“上一次成功 build 的开始时间之后”，第一次运行取最近 36 小时。
- 跨天比对默认 7 天；原始文章保留 30 天，事件和各期网页永久保留。
- 提醒邮件含要闻前 5 条标题和链接 `https://news.panellab.cn/<期号>/`；当天那期不存在时不发信、退出码 2。
- 退出码：0 成功；2 没有可提醒的期（notify）或设置不合法；3 部分降级（超出调用上限、或一半以上来源失败）；1 其他失败。
- 所有页面、邮件中的外来文本都必须转义。

## Review Focus

1. 某个来源返回坏 XML、HTML 错误页、超大响应或超时 → 跳过该来源并在作业输出里列出，整次 build 继续。（Task 2 测试）
2. DeepSeek 返回非 JSON、缺字段、429/5xx → 有限重试后对该批退回降级结果，不让整次 build 崩溃。（Task 4、Task 7 测试）
3. 同一天 build 跑两次（Agent 重试或手动） → 同一期被原子地覆盖，事件不重复、不会把自己当成“昨天的旧闻”。（Task 9 测试）
4. 新内容很少或为零（节假日、来源全挂） → 仍发布一期，显示「今天没有新的要闻」，提醒照发；来源全挂时退出码 3。（Task 8、Task 9 测试）
5. 标题或摘要里带 `<script>`、引号、`&` → 网页和邮件里都被转义，链接只允许 http/https。（Task 8、Task 10 测试）

---

## 文件结构

```
apps/dailynews/
  README.md                 程序说明、本地运行、测试
  pytest.ini
  install.sh                服务器安装/升级（sudo）
  make_bundle.py            在 Mac 上打包 dist/dailynews-<版本>.tar.gz
  default_settings.json     默认设置（含来源清单），安装时若无 settings.json 则复制
  dailynews/
    __init__.py             __version__ = "0.1.0"
    __main__.py             -> cli.main()
    cli.py                  子命令：build / notify / check-settings / check-sources
    settings.py             读取与校验设置
    store.py                SQLite 表与读写
    http.py                 受限的 HTTP GET/POST
    feeds.py                RSS 2.0 / Atom 解析
    extract.py              HTML 正文提取
    llm.py                  OpenAI 兼容客户端 + 调用计数
    cluster.py              同日归并、跨天比对
    rank.py                 分类、过滤、打分
    summarize.py            中文摘要
    render.py               静态网站生成
    notify.py               提醒邮件
    pipeline.py             build 编排、期号
    templates/              page.html、archive.html、style.css、app.js
  tests/
    conftest.py             临时目录、假时钟、FakeLLM、本地 HTTP 服务器夹具
    fixtures/               录好的 RSS/Atom/HTML 样本
    test_*.py
docs/user/dailynews.md      安装、域名、nginx、证书、密码、作业配置、日常使用
.github/workflows/ci.yml    新增 dailynews 作业（Python 3.9）
```

---

### Task 1: 包骨架、设置与 CI

**Files:**
- Create: `apps/dailynews/{pytest.ini,README.md,default_settings.json}`, `apps/dailynews/dailynews/{__init__.py,__main__.py,cli.py,settings.py}`, `apps/dailynews/tests/{conftest.py,test_settings.py}`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Produces:
  - `Settings`（dataclass，冻结）字段：`timezone: str`、`site_url: str`、`site_dir: str`、`data_dir: str`、`lookback_days: int = 7`、`categories: list[Category]`、`sources: list[Source]`、`keywords_follow: list[str]`、`keywords_block: list[str]`、`filter_china_domestic: bool = True`、`top_count: int = 8`、`per_category: int = 8`、`fetch_articles_max: int = 40`、`llm: LLMSettings`、`smtp: SMTPSettings`、`notify_top: int = 5`、`default_theme: str = "light"`。
  - `Category(id: str, name: str, weight: float)`；默认类别 `politics 政治`、`world 国际`、`tech 科技`、`ai AI`、`finance 财经`。
  - `Source(id: str, name: str, url: str, kind: str, category: str, type: str, enabled: bool = True)`；`kind` ∈ `rss|atom|html`；`type` ∈ `agency|media|official|company`。
  - `LLMSettings(base_url, model, max_calls_per_day=2000, timeout=60)`；`SMTPSettings(host, port, security, username, sender, recipients: list[str])`（与 Agent 告警的字段含义相同）。
  - `load_settings(path: str) -> Settings`，不合法时抛 `SettingsError(field: str, message: str)`，`str()` 形如 `sources[3].url: 必须是 http 或 https 地址`。
  - CLI：`python -m dailynews --settings PATH <command>`，默认设置路径 `/srv/dailynews/settings.json`；`check-settings` 合法时打印 `设置有效：N 个来源，M 个类别` 并退出 0，否则打印错误退出 2。

- [ ] **Step 1: Write the failing tests** `test_settings.py`：`test_default_settings_are_valid`（加载 `default_settings.json` 成功，至少 20 个来源，每个来源的 `category` 都在类别里，所有 `type` 合法）；`test_unknown_category_rejected`（字段路径 `sources[0].category`）；`test_non_http_url_rejected`；`test_duplicate_source_id_rejected`；`test_bad_timezone_rejected`；`test_secrets_not_allowed_in_file`（设置里出现 `api_key` 或 `password` 键 → `SettingsError`，提示用环境变量）；`test_check_settings_cli_exit_codes`（0 与 2）。
- [ ] **Step 2: Run** `cd apps/dailynews && python -m pytest -q` → 失败（模块不存在）。
- [ ] **Step 3: Implement** `settings.py`、`cli.py`（argparse 子命令分发，其它子命令先占位）、`default_settings.json`。来源清单按设计文档“初始清单”写入，每个来源填 RSS/Atom 地址；AI 公司没有订阅的先填 `kind: "html"` 及其新闻页地址（Task 2 实现 html 列表页解析）。`site_url` 默认 `https://news.panellab.cn`。
- [ ] **Step 4: Run** 测试通过。
- [ ] **Step 5: CI** 在 `ci.yml` 加 `dailynews` 作业：Python 3.9，`pip install pytest`，`cd apps/dailynews && python -m pytest -q`。
- [ ] **Step 6: Commit** `feat(dailynews): package skeleton, settings validation, CI job`

### Task 2: HTTP、来源解析与采集入库

**Files:**
- Create: `dailynews/{http.py,feeds.py,store.py}`, `tests/{test_http.py,test_feeds.py,test_store.py}`, `tests/fixtures/{bbc_world.xml,guardian_world.xml,atom_sample.xml,company_news.html,broken.xml}`
- Modify: `dailynews/cli.py`（`check-sources`）

**Interfaces:**
- Consumes: `Settings`, `Source`。
- Produces:
  - `http.get(url: str, *, timeout: float = 20, max_bytes: int = 5_000_000) -> Response(status: int, url: str, body: bytes, content_type: str)`；只允许 http/https、最多 5 次重定向且重定向目标也须 http/https；超过 `max_bytes` 抛 `FetchError`；固定 User-Agent `PanelLab-DailyNews/<版本> (+https://news.panellab.cn)`。`http.post_json(url, payload: dict, headers: dict, timeout) -> dict`。
  - `feeds.parse(source: Source, body: bytes, fetched_at: datetime) -> list[Item]`；`Item(source_id, url, title, summary, published_at: datetime | None, lang: str | None)`。支持 RSS 2.0、Atom、`kind: html`（从新闻列表页的 `<a>` 中取同站点文章链接与标题，最多 30 条）。HTML 标签从 summary 中剥掉。URL 规范化：去掉 `utm_*`、`fbclid`、片段。
  - `Store(path: str)`：建表 `articles(id, source_id, url UNIQUE, title, summary, published_at, fetched_at, body_text, body_status)`、`events(id, issue_date, title_zh, title_orig, category, status, first_issue, prev_event_id, score, summary_zh, detail_zh, change_zh, degraded)`、`event_articles(event_id, article_id)`、`issues(date PRIMARY KEY, built_at, item_count, hidden_count, status)`、`builds(id, started_at, ended_at, issue_date, status, note)`、`llm_calls(day, count)`。方法：`add_articles(items) -> list[int]`（已存在的 URL 跳过）、`articles_since(t) -> list[Article]`、`last_successful_build() -> Build | None`、`prune(now)`。
  - `collect(settings, store, since: datetime, now: datetime) -> CollectReport(ok: list[str], failed: dict[str, str], new_articles: int)`：逐个来源抓取，同一域名间隔至少 1 秒，单个来源失败不影响其它来源；只保留 `published_at` 为空或晚于 `since` 的条目。
  - CLI `check-sources`：对每个启用来源抓取并解析，打印 `来源 ID、状态、条目数`，全部可用时退出 0，否则 3。
- [ ] **Step 1: Write the failing tests**：`test_parse_rss_fixture`（标题、链接、时间、剥标签）、`test_parse_atom_fixture`、`test_parse_html_listing_same_site_only`、`test_broken_xml_raises_parse_error`、`test_url_normalization_strips_tracking`、`test_get_rejects_non_http_redirect`、`test_get_enforces_max_bytes`（用 conftest 本地 HTTP 服务器）、`test_collect_continues_after_source_failure`（一个来源 500、一个返回 `broken.xml`、一个正常 → `ok` 1 个、`failed` 2 个、文章入库）、`test_collect_skips_existing_urls`、`test_collect_respects_since`、`test_prune_drops_old_articles_keeps_events`（31 天前的文章删除，引用它的事件与 `event_articles` 之外的事件字段保留）。
- [ ] **Step 2: Run** → 失败。
- [ ] **Step 3: Implement** 上述接口。XML 用 `xml.etree.ElementTree`，解析前拒绝含 `<!DOCTYPE`/`<!ENTITY` 的文档（防实体展开）。
- [ ] **Step 4: Run** 通过。
- [ ] **Step 5: Commit** `feat(dailynews): fetch and parse sources into the store`

### Task 3: 原文正文提取

**Files:** Create `dailynews/extract.py`, `tests/test_extract.py`, `tests/fixtures/{article_bbc.html,article_guardian.html,paywall.html}`

**Interfaces:**
- Produces: `extract_text(html: bytes, url: str) -> str | None`：返回正文前若干段（合计不超过 4,000 字符），不足 300 字符视为提取失败返回 `None`。`fetch_bodies(store, settings, article_ids: list[int]) -> None`：最多 `fetch_articles_max` 篇，写 `body_text` 和 `body_status`（`ok|failed|short`）。
- [ ] **Step 1: Tests**：`test_extracts_article_paragraphs`（两个样本各包含已知首段）、`test_skips_nav_and_scripts`、`test_paywall_returns_none`、`test_fetch_bodies_caps_count_and_records_status`。
- [ ] **Step 2–4:** 失败 → 实现（`html.parser` 收集 `<article>` 内的 `<p>`；无 `<article>` 时取 `<p>` 文本最长的容器）→ 通过。
- [ ] **Step 5: Commit** `feat(dailynews): extract article text`

### Task 4: 大模型客户端与调用上限

**Files:** Create `dailynews/llm.py`, `tests/test_llm.py`; Modify `tests/conftest.py`（`FakeLLM`）

**Interfaces:**
- Produces:
  - `class LLM: def __init__(self, settings: LLMSettings, api_key: str, store: Store, today: date)`；`def chat_json(self, system: str, user: str, *, max_tokens: int = 2000) -> dict`：发送 `response_format: {"type": "json_object"}`，`temperature: 0.2`；429/5xx/超时最多重试 2 次（间隔 2 s、6 s）；返回值不是 JSON 对象时重试 1 次后抛 `LLMError`；每次实际请求在 `llm_calls` 计数，达到 `max_calls_per_day` 时抛 `BudgetExceeded`（`LLMError` 的子类）。
  - `api_key` 只来自环境变量 `DAILYNEWS_LLM_API_KEY`；缺失时 `build` 打印「缺少 DAILYNEWS_LLM_API_KEY」并退出 2。错误信息里不出现密钥或响应原文。
  - `FakeLLM`：按调用顺序返回预设 dict，或按 system 提示里的任务名（`cluster`、`link`、`classify`、`summarize`）调用测试提供的函数。
- [ ] **Step 1: Tests**（本地假服务器扮演 OpenAI 兼容接口）：`test_chat_json_sends_model_and_json_mode`、`test_retries_on_429_then_succeeds`、`test_non_json_raises_after_one_retry`、`test_budget_exceeded`、`test_key_never_in_error_text`。
- [ ] **Step 2–4:** 失败 → 实现 → 通过。
- [ ] **Step 5: Commit** `feat(dailynews): OpenAI-compatible client with daily call cap`

### Task 5: 同日归并与跨天比对

**Files:** Create `dailynews/cluster.py`, `tests/test_cluster.py`

**Interfaces:**
- Consumes: `Store`, `LLM`（或 `FakeLLM`）。
- Produces:
  - `group_same_day(articles: list[Article], llm) -> list[list[int]]`：先按规范化标题相似度（`difflib.SequenceMatcher`，阈值 0.6）与共享链接初分组，再把候选组分批（每批最多 40 条标题）交给大模型任务 `cluster` 确认/拆分；大模型失败时保留初分组。
  - `link_previous(groups, store, issue_date, lookback_days, llm) -> list[Linked]`；`Linked(article_ids, status: "new"|"followup"|"stale", prev_event_id: int | None)`。候选旧事件：最近 `lookback_days` 天、**期号早于当前期号**的事件中标题相似度 ≥ 0.35 的前 5 个；大模型任务 `link` 判定“同一事件且无实质新进展 / 后续有新进展 / 无关”。大模型失败时：相似度 ≥ 0.8 记 `stale`，其余 `new`。
- [ ] **Step 1: Tests**：`test_same_story_from_two_sources_grouped`、`test_unrelated_titles_not_grouped`、`test_llm_split_overrides_prefilter`、`test_followup_links_previous_event`、`test_no_progress_is_stale`、`test_same_issue_events_never_count_as_previous`（Review Focus 3）、`test_llm_failure_falls_back_to_similarity`。
- [ ] **Step 2–4:** 失败 → 实现 → 通过。
- [ ] **Step 5: Commit** `feat(dailynews): same-day grouping and cross-day follow-ups`

### Task 6: 分类、过滤与排序

**Files:** Create `dailynews/rank.py`, `tests/test_rank.py`

**Interfaces:**
- Produces: `classify(groups: list[Linked], store, settings, llm) -> list[Candidate]`；`Candidate(linked, category: str, china_domestic: bool, importance: int)`，大模型任务 `classify` 按批返回每组的 `category`（必须在设置的类别里，否则取首篇文章来源的类别）、`china_domestic`、`importance`（1–5）。大模型失败时：类别取来源类别、`china_domestic=False`、`importance=2`。
  `select(candidates, settings) -> Selection(top: list[Candidate], by_category: dict[str, list[Candidate]], hidden_stale: list[Candidate], filtered: int)`：
  - 去掉 `stale`（进 `hidden_stale`）；`filter_china_domestic` 为真时去掉 `china_domestic`（计入 `filtered`）；命中 `keywords_block` 的去掉。
  - 分数 = `importance × 类别权重 + 0.5 × (来源数 − 1)，上限 +2` ＋ 命中 `keywords_follow` 时 +2；`followup` 不加不减。
  - `top` 取分数最高的 `top_count` 个；其余按类别各取前 `per_category` 个，同一事件不在两处重复出现。
- [ ] **Step 1: Tests**：`test_domestic_china_filtered_but_international_kept`、`test_block_keyword_removes`、`test_follow_keyword_boosts`、`test_multi_source_bonus_capped`、`test_top_not_repeated_in_categories`、`test_invalid_llm_category_falls_back_to_source`、`test_stale_goes_to_hidden`。
- [ ] **Step 2–4:** 失败 → 实现 → 通过。
- [ ] **Step 5: Commit** `feat(dailynews): classify, filter and rank events`

### Task 7: 中文摘要

**Files:** Create `dailynews/summarize.py`, `tests/test_summarize.py`

**Interfaces:**
- Produces: `summarize(selection, store, llm) -> list[EventOut]`；`EventOut(title_zh, title_orig, category, status, summary_zh, detail_zh, change_zh, sources: list[SourceLink(name, url)], prev_issue: str | None, degraded: bool, basis: "body"|"snippet")`。
  - 每批最多 6 个事件交给大模型任务 `summarize`。系统提示固定要求：只依据给出的原文与片段，不添加原文没有的事实；输出简体中文；`summary_zh` 2–3 句；`detail_zh` 不超过 6 句，综合多个来源；`followup` 另给 `change_zh` 一句说明新变化；`title_zh` 为中文标题。
  - 依据只有标题和简介时 `basis="snippet"`，页面标注「仅据标题和简介」。
  - 大模型失败或 `BudgetExceeded`：`title_zh` 取原标题，`summary_zh` 取原文片段首句，`degraded=True`（页面标注「未生成摘要」）。
- [ ] **Step 1: Tests**：`test_summary_fields_from_llm`、`test_followup_has_change_line`、`test_snippet_basis_marked`、`test_budget_exceeded_degrades_remaining_only`（前一批成功、后一批降级）、`test_malformed_llm_item_degrades_that_event_only`。
- [ ] **Step 2–4:** 失败 → 实现 → 通过。
- [ ] **Step 5: Commit** `feat(dailynews): Chinese summaries with graceful fallback`

### Task 8: 静态网站

**Files:** Create `dailynews/render.py`, `dailynews/templates/{page.html,archive.html,style.css,app.js}`, `tests/test_render.py`

**Interfaces:**
- Produces: `render_issue(issue_date: date, events: list[EventOut], hidden_stale: list[Candidate], settings, site_dir: str) -> None`、`render_index(store, site_dir)`：
  - `site/<YYYY-MM-DD>/index.html` 与 `site/<YYYY-MM-DD>/issue.json`；`site/index.html` 跳到最新一期；`site/archive.html` 按月列出各期；`site/assets/` 放 CSS/JS（文件名带内容哈希）。
  - 先写到 `site/.tmp-<随机>/` 再 `os.replace` 到位，任何时刻读者看到的都是完整的一期（Review Focus 3）。
  - 页面：顶部期号与「要闻」，下面按类别分组；类别筛选按钮（全部 + 每个类别）；每条显示中文标题、摘要、来源名链接，点击展开详情、原标题、多来源链接；`followup` 显示「后续」标签、`change_zh` 和指向前一期的链接；页底「已隐藏 N 条旧闻」可展开列出标题；没有条目时显示「今天没有新的要闻」（Review Focus 4）。
  - 浅色/深色主题切换，用 `localStorage` 记住（读写包在 try/catch 中），初始取 `default_theme`，系统深色时跟随系统。
  - 手机宽度（375 px）可用；不加载任何外部脚本、字体或统计。
  - 所有外来文本 `html.escape`；链接只输出 http/https，其它丢弃（Review Focus 5）。
- [ ] **Step 1: Tests**：`test_issue_page_has_top_and_categories`、`test_script_in_title_is_escaped`、`test_javascript_url_dropped`、`test_followup_links_previous_issue`、`test_empty_issue_message`、`test_index_points_to_latest`、`test_archive_lists_issues`、`test_publish_is_atomic`（模拟写到一半抛错 → 旧页面仍完整）、`test_no_external_resources`。
- [ ] **Step 2–4:** 失败 → 实现 → 通过。
- [ ] **Step 5: 样稿确认（检查点）** 用 fixtures 跑一次 `render_issue` 生成样例页面，把截图（Mac 宽度和手机宽度、浅色和深色）给用户看；按用户意见调整样式后再提交。用户确认前不进入 Task 9。
- [ ] **Step 6: Commit** `feat(dailynews): static issue pages, archive and themes`

### Task 9: build 编排与期号

**Files:** Create `dailynews/pipeline.py`, `tests/test_pipeline.py`; Modify `dailynews/cli.py`

**Interfaces:**
- Consumes: Tasks 2–8 的全部接口。
- Produces:
  - `issue_date_for(now_local: datetime) -> date`：本地时间 12:00 及以后取次日，否则当天。
  - `run_build(settings, store, llm, now: datetime) -> BuildResult(issue_date, exit_code, note)`：记 `builds` 行 → `collect`（`since` = 上次成功 build 的 `started_at`，没有则 `now − 36h`）→ `group_same_day`（基于标题和 RSS 片段）→ `link_previous` → `fetch_bodies`（只取未判为 stale 的组的文章，按组内来源数和来源类型 agency/official 优先）→ `classify`/`select` → `summarize` → 把事件写入 `events`（同一期号先删旧再写，事务内完成）→ `render_issue`/`render_index` → `prune`。
  - 退出码：全部正常 0；一半以上来源失败、全部来源失败或发生 `BudgetExceeded` 时 3；设置或密钥缺失 2；其它异常 1（作业输出只打印异常类型和阶段名）。
  - 作业输出（stdout）是几行中文摘要：`期号 / 新文章 N / 事件 N（后续 N，隐藏旧闻 N，过滤国内 N）/ 来源失败：…/ 大模型调用 N 次`。
  - CLI `build [--now ISO时间]`（`--now` 仅用于测试和补跑）。
- [ ] **Step 1: Tests**（FakeLLM + 本地 HTTP 夹具，端到端）：`test_issue_date_rule`（11:59 → 当天，12:00 → 次日，23:20 → 次日，00:30 → 当天）、`test_build_end_to_end_publishes_issue`、`test_second_build_same_issue_replaces_not_duplicates`（Review Focus 3）、`test_next_day_marks_followup_and_hides_stale`、`test_all_sources_down_publishes_empty_issue_exit_3`（Review Focus 4）、`test_since_uses_last_successful_build`、`test_missing_api_key_exit_2`、`test_output_never_contains_api_key`。
- [ ] **Step 2–4:** 失败 → 实现 → 通过。
- [ ] **Step 5: Commit** `feat(dailynews): build pipeline and issue dates`

### Task 10: 提醒邮件

**Files:** Create `dailynews/notify.py`, `tests/test_notify.py`; Modify `dailynews/cli.py`

**Interfaces:**
- Produces: `run_notify(settings, store, now: datetime, password: str) -> int`：期号 = `now_local.date()`；该期 `issues.status` 不是 `ok`/`degraded` → 打印「<期号> 的简报尚未生成，不发提醒」返回 2。否则发一封 `multipart/alternative`（纯文本 + HTML）邮件：主题 `每日简报 <期号>：<第一条要闻中文标题>`；正文列出前 `notify_top` 条的中文标题和一句摘要，以及链接 `<site_url>/<期号>/`；SMTP 方式与 Agent 告警相同（`ssl` 465 / `starttls` / `none`），超时 60 s。密码只来自 `DAILYNEWS_SMTP_PASSWORD`。
- [ ] **Step 1: Tests**（本地调试 SMTP 服务器夹具）：`test_sends_top5_with_link`、`test_missing_issue_exit_2_no_mail`、`test_html_escaped_in_mail`（Review Focus 5）、`test_degraded_issue_still_notifies`、`test_password_not_in_output_on_auth_error`。
- [ ] **Step 2–4:** 失败 → 实现 → 通过。
- [ ] **Step 5: Commit** `feat(dailynews): reminder email`

### Task 11: 安装、打包、实验室验证与用户文档

**Files:** Create `apps/dailynews/{install.sh,make_bundle.py}`, `apps/dailynews/tests/test_install_static.py`, `docs/user/dailynews.md`; Modify `apps/dailynews/README.md`, `scripts/verify_agent_lab.py`（新增 `--dailynews` 模式）, `docs/project/feature-roadmap.md`（DailyNews 状态行）

**Interfaces:**
- Produces:
  - `python3 apps/dailynews/make_bundle.py` → `apps/dailynews/dist/dailynews-<版本>.tar.gz`（确定性：固定顺序、mtime 0、root 属主；不含 tests、`__pycache__`）；`dist/` 加入 `.gitignore`。
  - `sudo bash install.sh`：要求 root、已存在 `panellab` 用户（即已装 Agent），否则报中文错误退出；把代码装到 `/srv/dailynews/app/`（先写到临时目录再替换）；建 `/var/lib/dailynews`、`/srv/dailynews/site`（权限见 Global Constraints）；`settings.json` 不存在时从 `default_settings.json` 复制，存在则**不覆盖**；最后以 `panellab` 身份运行 `check-settings`。打印下一步：在 Agent 页面创建的两个作业（见下）。`--uninstall` 删除程序但保留数据、设置和网站；不碰 nginx。
  - 两个作业（文档给出可照填的字段）：
    - `dailynews-build`：命令 `["/usr/bin/python3","-m","dailynews","build"]`，工作目录 `/srv/dailynews/app`，计划 `20 23 * * *` 时区 `Asia/Shanghai`，超时 1800 s，重试 2 次间隔 600 s，补跑窗口 60 分钟，秘密环境变量 `DAILYNEWS_LLM_API_KEY`。
    - `dailynews-notify`：命令 `["/usr/bin/python3","-m","dailynews","notify"]`，计划 `0 0 * * *` 时区 `Asia/Shanghai`，超时 120 s，重试 1 次间隔 300 s，补跑窗口 60 分钟，秘密环境变量 `DAILYNEWS_SMTP_PASSWORD`。
  - `docs/user/dailynews.md`（中文，分步，用户自己执行 sudo）：
    1. 在阿里云 DNS 为 `news.panellab.cn` 加 A 记录指向服务器公网 IP，安全组放行 80/443；
    2. 打包、`scp` 上传、解包、`sudo bash install.sh`；
    3. `sudo apt install certbot python3-certbot-nginx apache2-utils`（已装则跳过）；
    4. `sudo htpasswd -c /etc/nginx/dailynews.htpasswd <用户名>`；
    5. 给出完整的 nginx `server` 块（`root /srv/dailynews/site;`、`auth_basic`、`index index.html`、`try_files $uri $uri/ =404;`、`add_header X-Robots-Tag "noindex, nofollow"`、安全响应头），放 `/etc/nginx/sites-available/dailynews` 并启用；`sudo nginx -t && sudo systemctl reload nginx`；
    6. `sudo certbot --nginx -d news.panellab.cn`；
    7. 在 Agent 页面创建两个作业、填秘密环境变量、点「立即运行」`dailynews-build`，打开 `https://news.panellab.cn` 验证；
    8. 设置文件怎么改（`sudo -u panellab nano /srv/dailynews/settings.json` 后运行 `check-settings`）；
    9. 与旧版并行 7 天的观察要点，以及之后停旧定时器的命令（引用 `server-agent.md` 现有段落）；
    10. 故障排查：退出码 2/3 的含义、「未生成摘要」、「仅据标题和简介」。
  - 实验室 `verify_agent_lab.py --dailynews`：在现有 systemd 夹具中安装 Agent 与 DailyNews，夹具内起本地 HTTP 服务器提供来源样本和假的 OpenAI 兼容接口、调试 SMTP；经 `panellab-agentctl` 创建两个作业并手动运行；断言网站文件生成且权限正确、提醒邮件收到、`build` 失败时 Agent 发出告警。只使用 `panellab-agent-lab*` 资源。
- [ ] **Step 1: Tests** `test_install_static.py`：脚本以 `set -eu` 开头、检查 root 与 `panellab` 用户、不包含 `nginx`、不覆盖已有 `settings.json`（静态检查对应分支）、`make_bundle.py` 两次输出字节相同且不含 `tests/`。
- [ ] **Step 2–4:** 失败 → 实现 → 通过。
- [ ] **Step 5: 来源核实** 在 Mac 上运行 `python -m dailynews --settings default_settings.json check-sources`；不可用的来源换成同等级来源或删除，把最终清单（名称、类型、类别）交给用户过目后提交。
- [ ] **Step 6: Run** `python3 scripts/verify_agent_lab.py --dailynews` → `dailynews lab: all checks passed`，且没有残留实验室容器。
- [ ] **Step 7: Commit** `feat(dailynews): installer, bundle, lab check and user guide`

## 最终验证

- `cd apps/dailynews && python3.9 -m pytest -q`（或 CI 的 3.9）全部通过；agent、backend、frontend 原有测试不受影响。
- `python3 scripts/verify_agent_lab.py --dailynews` 通过。
- 用 DeepSeek 真实密钥在 Mac 上对真实来源跑一次 `build`（`--settings` 指向本地临时设置、`site_dir` 指向临时目录），人工看一期：摘要是否忠于原文、去重和国内过滤是否合理。这一步需要用户提供密钥时，由用户在自己的终端里设置环境变量后运行。
- 用户验收：按 `docs/user/dailynews.md` 在阿里云安装、配置 nginx 和证书，与旧版并行 7 天。
