# 服务器 Agent 第一版 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal：** 在服务器上提供一个常驻的 PanelLab Agent，负责调度、记录运行历史和发告警邮件；Mac 上的 PanelLab 经已固定指纹的 SSH 查看并控制它。

**Architecture：**
- 新增顶层目录 `agent/`：只用标准库的 Python 包 `panellab_agent`，包含守护进程和 `panellab-agentctl`，配一个 systemd 单元和 `install.sh`。守护进程只在 Unix socket 上监听。
- Mac 后端在 SSH 上执行 `panellab-agentctl`，经 stdin/stdout 交换一行 JSON。
- 前端新增「服务器 Agent」页面。

**Tech Stack：**
- Agent：Python ≥ 3.9 标准库（sqlite3、smtplib、zoneinfo、socketserver、subprocess、urllib），用 pytest 测试。
- 后端：FastAPI、Paramiko。
- 前端：Vue 3 + Vitest。
- 端到端：Docker（Colima）、systemd 夹具。

**Spec：** [design-server-agent.md](design-server-agent.md)

## Global Constraints

- Agent 代码只能使用 Python 标准库，语法与 API 兼容 Python 3.9：不用 `match`，运行时不用 `X | Y` 类型写法。
- 系统用户和组为 `panellab`，不能占用或改动服务器上已有的 `agent` 账户。`panellab` 不加入 sudoers。
- 路径：
  - 程序：`/opt/panellab-agent/<版本>/`，`/opt/panellab-agent/current` 链接到当前版本；
  - 数据：`/var/lib/panellab-agent/`；
  - 配置：`/etc/panellab-agent/agent.json`；秘密：`/etc/panellab-agent/secrets.json`，权限 0600；
  - socket：`/run/panellab-agent/agent.sock`，权限 0660，组 `panellab`；
  - 控制命令：`/usr/local/bin/panellab-agentctl`。
- 不开 TCP 端口。唯一的出站请求是 SMTP 和用户填写的心跳 URL。
- 协议：`PROTOCOL_VERSION = 1`。
  - 请求：`{"protocol_version":1,"op":str,"args":{}}`。
  - 响应：成功为 `{"ok":true,"result":…}`，失败为 `{"ok":false,"error":{"code":str,"message":str}}`。
- 默认值：

  | 项目 | 默认值 |
  | --- | --- |
  | 超时 | 3600 s |
  | 超时后发 SIGKILL 前的宽限 | 30 s |
  | 单次运行输出上限 | 10 MiB |
  | 每个作业保留 | 200 次或 90 天，取较多者 |
  | 补跑窗口 | 60 min |
  | 判为 `missed` 的延迟 | 15 min |
  | 持续告警的提醒 | 每 24 h 一次 |
  | 磁盘告警 | > 90% 触发，< 85% 恢复 |
  | CPU / 内存告警 | 每 30 s 采样；连续 10 min > 95% 触发，连续 5 min < 90% 恢复 |
  | SSH 告警 | 10 min 内失败 > 30 次；信中列出前 5 个 IP |
  | 心跳 | 每 5 min |
  | 判定 Agent 停机 | 心跳间隔 > 5 min |
  | 告警信中的输出 | 最近 20 行 |
  | SMTP 超时 | 20 s |

- 运行状态取值：`running`、`succeeded`、`failed`、`timed_out`、`skipped_overlap`、`missed`、`agent_lost`、`cancelled`。
- 触发方式取值：`schedule`、`manual`、`retry`、`catch_up`。
- 秘密（SMTP 密码、`secret_env` 的值）只存在服务器上，任何读取操作都不返回它们，PanelLab 也不保存。
- PanelLab 从不代用户输入或保存 sudo 密码，也不连接用户的真实服务器做测试。
- 界面和邮件文字使用中文，错误码使用英文。
- 每完成一个任务提交一次，提交信息以 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` 结尾。

## Review Focus

1. 作业参数包含空格、中文、引号或 `$(...)` 时，必须原样传给程序，绝不能经过 shell 解释（Task 4）。
2. 作业时区与服务器时区不同，或遇到夏令时切换：不存在的本地时间跳过，重复的本地时间只运行一次（Task 1）。
3. 两处同时编辑同一作业，后提交的一方 `revision` 已过期：返回 `conflict`，界面提示刷新，不能静默覆盖（Task 2、Task 10）。
4. 作业输出巨大、是二进制或不是合法 UTF-8：超过上限时截断并标注，读取时用替换字符解码，不能报错（Task 4、Task 9）。
5. SMTP 地址错误或无法连接：告警被记为“发送失败”，可以在状态中看到；守护进程不能卡住，调度照常进行（Task 5、Task 7）。

---

## 文件结构

```
agent/
  panellab_agent/__init__.py      版本号 __version__ = "0.1.0"、PROTOCOL_VERSION
  panellab_agent/clock.py         可注入时钟
  panellab_agent/cron.py          cron 解析与下次运行时间
  panellab_agent/store.py         SQLite：作业、运行、告警状态、元数据
  panellab_agent/secrets.py       secrets.json 读写（0600）
  panellab_agent/scheduler.py     纯函数：本轮应启动哪些运行、记哪些 missed
  panellab_agent/runner.py        子进程、超时、输出截断、重试
  panellab_agent/alerts.py        告警状态机 + 邮件发送
  panellab_agent/watchers.py      磁盘、CPU、内存、SSH 失败、心跳
  panellab_agent/protocol.py      请求校验与分发
  panellab_agent/daemon.py        主循环、socket 服务、启动恢复、排空
  panellab_agent/ctl.py           panellab-agentctl
  systemd/panellab-agent.service
  install.sh
  tests/…                         每个模块一个 test_*.py
backend/services/agent_client.py   通过 SSH 调用 ctl、上传安装包
backend/routers/agent.py           /api/servers/{id}/agent/...
backend/tests/test_agent_client.py, test_agent_api.py
frontend/src/composables/useAgentApi.js
frontend/src/views/dashboard/Agent.vue
frontend/src/components/agent/{JobList,JobDetail,RunOutput,JobForm,AlertSettings,AgentInstall}.vue
frontend/tests/agent.test.js, agent-forms.test.js
scripts/fixtures/agent_systemd/Dockerfile
scripts/verify_agent_lab.py
docs/user/server-agent.md, docs/project/server-agent-<日期>.md
```

---

### Task 1：Agent 包骨架与 cron 解析

**Files：**
- Create：`agent/panellab_agent/__init__.py`、`clock.py`、`cron.py`、`agent/tests/test_cron.py`、`agent/pytest.ini`
- Modify：`.github/workflows/ci.yml`（新增 `agent` 作业：Python 3.9，执行 `pip install pytest && cd agent && python -m pytest -q`）

**Interfaces：**
- Produces：
  - `clock.Clock`，有方法 `now() -> datetime`（带时区的 UTC）和 `monotonic() -> float`；`clock.FakeClock(start)` 增加 `advance(seconds)`。
  - `cron.parse(expr: str) -> CronSpec`，非法时抛 `CronError(message)`。
  - `cron.next_times(spec: CronSpec, after: datetime, tz: str, count: int) -> list[datetime]`，返回 UTC 时间，严格晚于 `after`。
  - `cron.validate_tz(tz: str)`，非法时抛 `CronError`。

- [ ] **Step 1：写失败测试** `agent/tests/test_cron.py`

```python
def test_daily_midnight_shanghai():
    s = parse("0 0 * * *")
    got = next_times(s, utc(2026,11,10,15,59), "Asia/Shanghai", 2)
    assert got == [utc(2026,11,10,16,0), utc(2026,11,11,16,0)]
def test_lists_ranges_steps(): assert [t.minute for t in next_times(parse("20,40,50 23 * * *"), utc(2026,11,10,15,0), "Asia/Shanghai", 3)] == [20,40,50]
def test_dom_dow_or_rule(): ...      # "0 9 13 * 5"：13 号或周五都触发（Vixie 规则）
def test_sunday_is_0_and_7(): assert next_times(parse("0 0 * * 7"), …) == next_times(parse("0 0 * * 0"), …)
def test_spring_forward_gap_skipped(): ...   # America/New_York 2027-03-14 02:30 不存在 → 当天不运行
def test_fall_back_runs_once(): ...          # 2026-11-01 01:30 出现两次 → 只运行一次（取第一次）
def test_leap_day(): ...                     # "0 0 29 2 *" 的下一次是 2028-02-29
@pytest.mark.parametrize("bad", ["", "* * * *", "60 * * * *", "* * 0 * *", "*/0 * * * *", "MON * * * *", "1-", "5-1 * * * *"])
def test_rejects(bad): with pytest.raises(CronError): parse(bad)
def test_bad_timezone(): with pytest.raises(CronError): validate_tz("Mars/Base")
```

- [ ] **Step 2：** 运行 `cd agent && python -m pytest tests/test_cron.py -q`，预期失败（ImportError）。
- [ ] **Step 3：实现。** 只支持数字、`*`、`,`、`-`、`/`，不支持月份和星期的英文名。按分钟在本地时区逐步推进，但应跳到下一个匹配的小时或日期，不要逐分钟遍历一整年。夏令时：用 `fold=0` 构造本地时间，再转 UTC 并转回；不相等说明该时间不存在，跳过；对于重复出现的时间，只取 `fold=0` 那一次。
- [ ] **Step 4：** 运行同一命令，预期通过。
- [ ] **Step 5：提交** `feat(agent): add the agent package and cron schedules`

### Task 2：存储与秘密

**Files：** Create `agent/panellab_agent/store.py`、`secrets.py`、`agent/tests/test_store.py`

**Interfaces：**
- Consumes：`Clock`。
- Produces：
  - `Store(path: str, clock: Clock)`，建表、开启 WAL。
  - 作业：
    - `create_job(job: dict) -> dict`、`update_job(job_id, changes: dict, revision: int) -> dict`、`delete_job(job_id)`；
    - `get_job(job_id) -> dict | None`、`list_jobs() -> list[dict]`；
    - `set_paused(job_id, paused: bool)`。
  - 运行：
    - `insert_run(job_id, trigger, scheduled_for, attempt) -> int`，返回 run_id；
    - `finish_run(run_id, status, exit_code, output_bytes, truncated)`；
    - `record_missed(job_id, scheduled_for)`；
    - `list_runs(job_id, status=None, limit=50, before_id=None) -> list[dict]`、`get_run(run_id)`；
    - `running_runs() -> list[dict]`、`last_scheduled_for(job_id) -> datetime | None`；
    - `prune(job_id)`。
  - 告警：`get_alert(key) -> dict | None`、`put_alert(key, state, since, last_sent_at, message)`、`firing_alerts()`。
  - 元数据：`get_meta(name)`、`set_meta(name, value)`。
  - 异常：`StoreError(code, message)`，code 取 `not_found`、`conflict`、`invalid`。
  - 秘密：
    - `Secrets(path)` 的 `get() -> dict`；
    - `set_job_secrets(job_id, changes: dict[str, str | None])`：值为 `None` 表示删除，键不出现表示不变；
    - `job_secret_names(job_id) -> list[str]`、`job_env(job_id) -> dict`；
    - `set_smtp_password(value | None)`、`smtp_password()`；
    - 写入时先写临时文件，再原子替换，权限 0600。

作业字段与校验依据设计文档：
- `id` 匹配 `^[a-z0-9][a-z0-9-]{0,47}$`；
- `command` 是非空字符串列表，且不含 NUL；
- `workdir` 是绝对路径；
- `schedule` 是 `{"cron": [str,…], "tz": str}`，`cron` 可以是空列表；
- 数值字段有上下限，例如 `timeout_seconds` 取 1–86400、`retries` 取 0–10；
- `alert` 是 `{"on_failure": bool, "on_recovery": bool, "include_output": bool}`；
- 保留 `settings_schema` 和 `settings_file`，不解释其内容。

- [ ] **Step 1：写失败测试**：
  - `test_create_and_get_roundtrip`
  - `test_update_requires_current_revision`：传入过期 revision → `StoreError.code == "conflict"`，成功后 revision + 1
  - `test_rejects_invalid_job[...]`：参数化覆盖上述每条校验
  - `test_duplicate_id_conflict`
  - `test_delete_keeps_history_but_hides_job`：删除后 `list_jobs` 不再出现该作业，但 `get_run` 仍能读到
  - `test_prune_keeps_200_or_90_days`：250 条运行全部在 30 天内 → 保留 250 条；250 条全部超过 100 天 → 保留 200 条
  - `test_secrets_file_mode_0600_and_never_in_sqlite`：在数据库文件的字节中找不到秘密的值
  - `test_secret_changes_keep_unchanged_and_delete_none`
- [ ] **Step 2：** 运行，预期失败。
- [ ] **Step 3：实现。** 每次调用使用独立连接，或用一个连接加 `threading.Lock`，因为 socket 线程和主循环会同时访问。
- [ ] **Step 4：** 运行，预期通过。
- [ ] **Step 5：提交** `feat(agent): store jobs, runs and secrets`

### Task 3：调度决策

**Files：** Create `agent/panellab_agent/scheduler.py`、`agent/tests/test_scheduler.py`

**Interfaces：**
- Consumes：`cron.next_times`、`Store.last_scheduled_for`。
- Produces：
  - `plan_tick(jobs: list[dict], last_seen: dict[str, datetime], now: datetime, running: set[str]) -> Decisions`；
  - `Decisions` 有三个字段：
    - `start: list[Start]`，`Start` 为 `(job_id, trigger, scheduled_for, late_minutes)`；
    - `missed: list[(job_id, scheduled_for)]`；
    - `skipped_overlap: list[(job_id, scheduled_for)]`。
- 规则：
  - 只考虑 `last_seen[job]` 与 `now` 之间到期的时间点。
  - 已暂停的作业：不启动，也不记 missed；恢复后从当时开始计算。
  - 到期时间在 `now` 之前 2 分钟以内的，算准时，`trigger=schedule`。
  - 在补跑窗口以内的，所有这类时间点合并成一次，以最晚的一个为准，`trigger=catch_up`，`late_minutes` 取整。
  - 超出窗口的，每个时间点各记一条 missed。
  - 作业正在运行时又到期，记 `skipped_overlap`。
  - `catch_up_window_minutes == 0` 时，所有迟到的时间点都记 missed。

- [ ] **Step 1：写失败测试**：
  - `test_on_time_start`
  - `test_downtime_within_window_merges_into_one_catch_up`：23:20、23:40、23:50 三个时间点，在 00:10 恢复 → 一次 catch_up，scheduled_for 为 23:50，迟到 20 分钟
  - `test_beyond_window_records_each_missed`：00:00 的时间点在 09:00 才处理 → missed，不启动
  - `test_window_zero_never_catches_up`
  - `test_overlap_skipped`
  - `test_paused_job_neither_runs_nor_misses`
  - `test_new_job_does_not_backfill_history`：`last_seen` 缺失时，以作业的 `created_at` 为起点
- [ ] **Step 2：** 运行，预期失败。
- [ ] **Step 3：实现。** 写成纯函数，不访问数据库，也不读取时钟。
- [ ] **Step 4：** 运行，预期通过。
- [ ] **Step 5：提交** `feat(agent): decide scheduled, late and missed runs`

### Task 4：运行器

**Files：** Create `agent/panellab_agent/runner.py`、`agent/tests/test_runner.py`

**Interfaces：**
- Consumes：`Store`、`Secrets.job_env`、`Clock`。
- Produces：
  - `Runner(store, secrets, clock, output_dir: str, on_finish: Callable[[dict, dict], None])`，其中 `on_finish(job, run)` 在一次尝试结束时调用。
  - `start(job: dict, trigger: str, scheduled_for: datetime | None, attempt: int = 1) -> int`，返回 run_id。
  - `poll()`：主循环每秒调用一次，负责收尾、超时和安排重试。
  - `cancel(run_id)`、`running_job_ids() -> set[str]`、`active_count() -> int`。
  - `read_output(run_id, offset: int, limit: int = 65536) -> {"data": str, "offset": int, "eof": bool, "truncated": bool}`。
- 行为：
  - 启动方式：`subprocess.Popen(job["command"], cwd=workdir, env=…, start_new_session=True, stdout=file, stderr=STDOUT, stdin=DEVNULL)`，不使用 shell。
  - 环境变量：从 `{"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": workdir, "LANG": "C.UTF-8"}` 开始，依次叠加 `env_file`（`KEY=VALUE` 行，忽略 `#` 注释）、`env`、secret env。
  - 输出：写入 `<output_dir>/<run_id>.log`。超过 10 MiB 后停止写入并截断，追加一行 `[输出超过 10 MiB，已截断]`。
  - 超时：向进程组发 SIGTERM，30 秒后发 SIGKILL，状态记为 `timed_out`。
  - 重试：失败且 `attempt <= retries` 时，在 `retry_delay_seconds` 后以 `trigger=retry`、`attempt + 1` 再次启动。只有最后一次尝试的结果才交给告警。
  - 工作目录不存在或命令找不到时，状态记为 `failed`，输出文件写入中文原因，不抛出异常。

- [ ] **Step 1：写失败测试**（用真实的 `/bin/sh` 子进程和短超时）：
  - `test_args_with_spaces_quotes_chinese_and_subshell_are_literal`：运行 `[sys.executable, "-c", "import sys,json;print(json.dumps(sys.argv[1:]))", "a b", "中文", "'q'", "$(id)"]`，输出等于这个列表
  - `test_success_and_exit_code`
  - `test_timeout_kills_process_group_including_children`：sh 启动一个后台 `sleep 60` 子进程，超时后整个组都不存在
  - `test_output_truncated_at_limit`：测试中把上限改为 1 KiB
  - `test_invalid_utf8_output_reads_with_replacement`
  - `test_retry_then_success_reports_only_final`：on_finish 只调用一次，且结果为 succeeded，attempt=2
  - `test_missing_workdir_is_failed_with_reason`
  - `test_secret_env_injected_but_not_logged`
  - `test_cancel_marks_cancelled`
- [ ] **Step 2：** 运行，预期失败。
- [ ] **Step 3：实现。**
- [ ] **Step 4：** 运行，预期通过。
- [ ] **Step 5：提交** `feat(agent): run jobs with timeouts, output limits and retries`

### Task 5：告警状态机与邮件

**Files：** Create `agent/panellab_agent/alerts.py`、`agent/tests/test_alerts.py`

**Interfaces：**
- Consumes：`Store` 中的告警方法、`Secrets.smtp_password`、`Clock`。
- Produces：
  - `AlertBook(store, clock, mailer, config_getter: Callable[[], dict], hostname: str)`；
    - `observe(key: str, firing: bool, subject: str, body: str)`：只在状态变化时发信，持续触发时每 24 小时提醒一次；
    - `notify(subject, body)`：一次性通知，用于 Agent 停机、补跑等。
  - `Mailer(config_getter, password_getter, clock)`：
    - `send(subject, body) -> None`：失败时抛 `MailError`；超时 20 秒；支持 STARTTLS、SSL 或明文（仅限本地测试）；
    - `last_result() -> {"at":…, "ok": bool, "error": str | None}`。
  - 邮件标题格式：`[PanelLab · <hostname>] <subject>`。
- 发信失败时只记录 `last_result`，不抛到调用方；告警状态照常推进。下一次状态变化或提醒到期时会再尝试发送，不做退避重发。

- [ ] **Step 1：写失败测试**（用 `aiosmtpd` 以外的方式：在测试里启动一个标准库 socket 小服务器，接收 SMTP 对话，或直接注入一个假 Mailer）：
  - `test_fires_once_and_recovers_once`
  - `test_flapping_within_state_does_not_resend`
  - `test_daily_reminder_after_24h`
  - `test_smtp_unreachable_records_failure_without_raising`：连接到一个已关闭的端口，`last_result().ok is False`，状态照样变为 firing
  - `test_no_recipients_configured_skips_quietly_and_reports`
  - `test_subject_prefix_and_chinese_body_utf8`
- [ ] **Step 2–4：** 运行失败 → 实现 → 运行通过。
- [ ] **Step 5：提交** `feat(agent): email alerts on state changes`

### Task 6：资源、SSH 与心跳检查

**Files：** Create `agent/panellab_agent/watchers.py`、`agent/tests/test_watchers.py`

**Interfaces：**
- Consumes：`AlertBook.observe`、`Clock`。
- Produces：
  - `DiskWatch(read_mounts, statvfs)`：`check(cfg) -> list[(key, firing, subject, body)]`，key 为 `disk:<挂载点>`；过滤 tmpfs、devtmpfs、overlay、squashfs、proc、sysfs、cgroup*、nsfs、ramfs，并按设备去重；有迟滞。
  - `SustainedWatch(name: str)`：`sample(percent: float, now) -> (firing: bool | None)`，按设计中的触发与恢复时长判定；`None` 表示状态未变。
  - `read_cpu_percent(prev, cur)` 和 `read_mem_percent(meminfo)`：纯函数，读取 `/proc/stat` 和 `/proc/meminfo` 的文本。
  - `SshWatch(read_lines: Callable[[datetime], list[str]])`：`check(now, cfg) -> (firing, subject, body)`。
    - 识别 `Failed password`、`Invalid user`、`authentication failure` 这几类日志行，提取 IP；
    - 数据源优先使用 `journalctl -u ssh -u sshd --since … -o cat`，失败时退回读取 `/var/log/auth.log` 的尾部。
  - `Heartbeat(url_getter, clock, opener=urllib.request.urlopen)`：`tick()` 每 5 分钟最多发一次 GET，超时 10 秒；只接受 https URL。

- [ ] **Step 1：写失败测试**：
  - `test_disk_hysteresis_90_85`
  - `test_disk_skips_pseudo_and_duplicates`
  - `test_cpu_needs_full_10_minutes`：9 分 30 秒时还不触发，10 分钟时触发；中途有一次低于 95% 则重新计时
  - `test_cpu_recovers_after_5_minutes_below_90`
  - `test_ssh_counts_last_10_minutes_top5`：使用固定样本行
  - `test_ssh_below_threshold_not_firing`
  - `test_heartbeat_disabled_without_url_and_rejects_http`
  - `test_heartbeat_failure_does_not_raise`
- [ ] **Step 2–4：** 运行失败 → 实现 → 运行通过。
- [ ] **Step 5：提交** `feat(agent): watch disk, CPU, memory, SSH failures and heartbeat`

### Task 7：守护进程、协议与 ctl

**Files：** Create `agent/panellab_agent/protocol.py`、`daemon.py`、`ctl.py`、`agent/tests/test_protocol.py`、`agent/tests/test_daemon.py`

**Interfaces：**
- Consumes：前面各任务提供的全部接口。
- Produces：
  - `protocol.handle(agent: Agent, request: dict) -> dict`。支持的 op：
    - `status`
    - `jobs.list`、`jobs.get`、`jobs.create`、`jobs.update`、`jobs.delete`
    - `jobs.run_now`、`jobs.pause`、`jobs.resume`
    - `jobs.preview`：参数 `{"schedule":…}`，返回接下来 3 次运行时间（ISO 格式，同时给出 UTC 和作业时区的本地时间）
    - `runs.list`、`runs.output`、`runs.cancel`
    - `alerts.get`、`alerts.configure`、`alerts.test_email`
    - `drain`

    错误码：`bad_request`、`protocol_mismatch`、`not_found`、`conflict`、`invalid`、`draining`、`internal`。
  - `Agent(config_path, data_dir, clock)`：`tick()` 每秒执行一轮：调度、`runner.poll`、观察器、心跳、`set_meta("last_alive", now)`。
  - `daemon.main(argv)`：
    - 参数：`--config`、`--data-dir`、`--socket`、`--foreground`；
    - socket 为 `ThreadingUnixStreamServer`，每个连接只处理一行请求和一行响应，请求上限 1 MiB；
    - 收到 SIGTERM 时排空后退出。
  - 启动时恢复：
    - 所有 `running` 状态的运行改为 `agent_lost`，并为每条发一封作业告警；
    - 如果 `now - last_alive > 5 min`，发通知“Agent 曾停止运行：从 X 到 Y”；
    - 然后用 `last_alive` 作为调度器的 `last_seen`，让补跑和 missed 的判定正常进行。
  - 告警接线（在 `Agent` 中完成）：
    - `runner` 的 `on_finish`：结果为 failed、timed_out、agent_lost 时，以 `job:<id>` 为键调用 `observe(firing=True)`；结果为 succeeded 时调用 `observe(firing=False)`。是否发信，以及恢复时是否发信，由作业的 `alert` 设置决定。
    - 调度器每记录一条 missed，同样以 `job:<id>` 为键触发告警。
    - 每次 catch_up 启动时，调用 `notify("补跑：<作业>迟到 N 分钟")`。
  - `ctl.main(argv)`：
    - 默认从 stdin 读一行 JSON，原样转发到 socket，再把响应写到 stdout，退出码 0；
    - `ctl drain [--force]` 会构造 drain 请求，并一直等到 `active_count == 0`；
    - socket 不存在时，向 stderr 写入 `{"code":"agent_unreachable"}`，退出码 3；
    - 权限不足时写入 `{"code":"agent_permission"}`，退出码 4。
  - `status` 的结果包含：
    - `version`、`protocol_version`、`started_at`、`hostname`、`server_time`、`timezone`；
    - `draining`、`job_count`、`running`；
    - `firing`（告警列表，每项含 key、since、message）；
    - `mail`（即 `last_result`）；
    - `alerts_configured: bool`。

- [ ] **Step 1：写失败测试**：
  - `test_protocol_mismatch`
  - `test_unknown_op_bad_request`
  - `test_create_run_now_and_read_output`：使用 FakeClock 和真实子进程
  - `test_update_conflict_maps_to_conflict_code`
  - `test_reads_never_contain_secrets`：设置 SMTP 密码和 secret_env 后，序列化后的 `status`、`jobs.get`、`alerts.get` 中都找不到秘密的值，只出现“已设置”标记和键名
  - `test_startup_marks_running_as_agent_lost_and_alerts`
  - `test_startup_reports_downtime_over_5_minutes`
  - `test_drain_refuses_new_runs`
  - `test_socket_roundtrip_via_ctl`：在 tmp 目录里起 socket，运行 `ctl.main` 并对 stdin 和 stdout 做 monkeypatch
  - `test_ctl_exit_codes_unreachable_3_permission_4`
  - `test_smtp_hang_does_not_block_tick`：Mailer 假装阻塞 25 秒，tick 仍能按时返回；发信放在后台单线程队列里执行
- [ ] **Step 2–4：** 运行失败 → 实现 → 运行通过。
- [ ] **Step 5：提交** `feat(agent): serve the control protocol from a daemon`

### Task 8：安装脚本、systemd 单元与 systemd 夹具

**Files：**
- Create：
  - `agent/systemd/panellab-agent.service`
  - `agent/install.sh`
  - `scripts/fixtures/agent_systemd/Dockerfile`
  - `scripts/verify_agent_lab.py`
  - `scripts/tests/test_agent_install_static.py`：用 `bash -n` 检查语法，并检查单元文件中的关键行
- 单元文件：
  - `User=panellab`、`Group=panellab`
  - `ExecStart=/usr/bin/python3 -m panellab_agent.daemon --config /etc/panellab-agent/agent.json --data-dir /var/lib/panellab-agent --socket /run/panellab-agent/agent.sock`
  - `Environment=PYTHONPATH=/opt/panellab-agent/current`
  - `RuntimeDirectory=panellab-agent`、`RuntimeDirectoryMode=0750`
  - `Restart=always`、`RestartSec=5`
  - `NoNewPrivileges=yes`、`ProtectSystem=full`、`PrivateTmp=yes`
  - `ReadWritePaths=/etc/panellab-agent`
  - `KillMode=control-group`、`TimeoutStopSec=infinity`（排空时需要等待作业结束）
- `install.sh` 的用法：
  - `sudo bash install.sh`：安装或升级。顺序：检查 Python ≥ 3.9 和 systemd → 建用户和组 → 把 `panellab` 加入 `adm` → 把 `$SUDO_USER` 加入 `panellab` → 复制程序到版本目录 → 切换 `current` 链接 → 升级时先执行 `panellab-agentctl drain` → 写入单元 → `daemon-reload` → `enable --now` / `restart`。
  - `--uninstall`：停止服务并删除程序和单元，保留数据。
  - `--uninstall --purge`：连 `/var/lib/panellab-agent` 和 `/etc/panellab-agent` 一起删除。
  - 必须幂等。最后打印“请重新登录 SSH 使组权限生效”。
  - 不改动名为 `agent` 的已有账户。
- 夹具：
  - 基于 `debian:bookworm` 镜像，安装 `systemd systemd-sysv openssh-server python3 sudo procps`，`CMD ["/lib/systemd/systemd"]`；
  - 建立 `admin` 用户，密码仅供夹具使用，有 sudo；另建一个名为 `agent` 的普通用户，用来证明不会被改动；
  - 容器内放一个用 Python 标准库写的调试 SMTP 接收器，作为 systemd 服务运行，监听 127.0.0.1:2525，把收到的邮件写入 `/var/mail-sink/`。
- `verify_agent_lab.py`：
  - 以 `--privileged --cgroupns=host -v /sys/fs/cgroup:/sys/fs/cgroup:rw` 启动夹具；
  - 打包 `agent/` 后复制进容器，以 admin 身份执行 `sudo bash install.sh`；
  - 断言：
    - 服务处于 active；
    - socket 的属主为 `panellab`，模式为 0660；
    - `id admin` 包含 `panellab`；
    - `id panellab` 包含 `adm`，且 `panellab` 不在 sudoers 中；
    - `agent` 账户没有变化；
  - 用 ctl 跑通流程：创建作业 → 立即运行 → 读取输出 → 制造失败 → 邮件接收器收到告警 → 成功一次 → 收到“已恢复”；
  - `kill -9` 守护进程后，确认 systemd 会重启它，并标记 `agent_lost`；
  - 一个作业正在运行时执行升级，确认作业跑完后服务才重启；
  - 依次执行卸载和 `--purge`；
  - 退出时删除容器。
- 若 Colima 上无法运行 systemd 容器，则以 `--foreground` 模式启动守护进程，复用现有的 `remote_workspace` 夹具验证协议和告警部分，并在记录中逐项列出未验证的 systemd 相关项。

- [ ] **Step 1：** 先写 `test_agent_install_static.py`，运行，预期失败。
- [ ] **Step 2：** 编写单元文件与 `install.sh`，静态测试通过。
- [ ] **Step 3：** 编写夹具和 `verify_agent_lab.py`，运行 `python3 scripts/verify_agent_lab.py`，预期最后输出 `agent lab: all checks passed`。
- [ ] **Step 4：提交** `feat(agent): install as a systemd service and verify in a systemd fixture`

### Task 9：后端 agent_client 与路由

**Files：**
- Create：`backend/services/agent_client.py`、`backend/routers/agent.py`、`backend/tests/test_agent_client.py`、`backend/tests/test_agent_api.py`
- Modify：`backend/main.py`（`include_router(agent.router)`）

**Interfaces：**
- Consumes：`remote_connection.open_remote`、`remote_deadline`、`fail`。执行方式参照 `remote_probe.collect_snapshot`。
- Produces：
  - `agent_client.call(db, user, server_id, op: str, args: dict | None = None) -> dict`：
    - 执行 `panellab-agentctl`，通过 `channel.sendall` 发送一行 JSON 后关闭写端，读取 stdout，上限 2 MiB，超时 30 秒；
    - 映射规则：
      - 退出码 127 或 stderr 含 `not found` → `fail('agent_not_installed', …, 404)`
      - 退出码 3 → `agent_unreachable`（503）
      - 退出码 4 → `agent_permission`（403），提示“请重新登录 SSH 或确认已加入 panellab 组”
      - 返回 `ok: false` 时 → `fail(error.code, error.message, 409 if code == 'conflict' else 422 if code in ('invalid', 'bad_request') else 503)`
      - `protocol_mismatch` → 提示“请升级服务器上的 Agent”
  - `agent_client.upload_bundle(db, user, server_id) -> {"path": str, "command": str}`：
    - 在内存中把仓库的 `agent/` 打包成 tar，排除 `tests/` 和 `__pycache__`；
    - 经 SFTP 上传到 `~/panellab-agent-<版本>/` 并在服务器上解包。解包时用固定命令 `tar -xf`，路径经 `shlex.quote` 处理；
    - 返回 `sudo bash ~/panellab-agent-<版本>/install.sh`。
    - Docker 镜像中需要包含 `agent/` 目录：修改 `Dockerfile`，加入 `COPY agent/ /app/agent/`。
  - 路由，前缀为 `/api/servers/{server_id}/agent`，权限与 `routers/remote.py` 相同：

    | 方法 | 路径 | 对应 op |
    | --- | --- | --- |
    | GET | `` | `status` |
    | GET / POST | `/jobs` | 列出 / 创建 |
    | GET / PUT / DELETE | `/jobs/{job_id}` | 读取 / 更新（PUT 的 body 带 `revision`） / 删除 |
    | POST | `/jobs/{job_id}/run`、`/pause`、`/resume` | 立即运行、暂停、恢复 |
    | GET | `/jobs/{job_id}/runs?status=&before=` | 运行历史 |
    | GET | `/runs/{run_id}/output?offset=` | 运行输出 |
    | POST | `/runs/{run_id}/cancel` | 取消运行 |
    | POST | `/schedule-preview` | 预览接下来的运行时间 |
    | GET / PUT | `/alerts` | 读取 / 修改告警设置 |
    | POST | `/alerts/test` | 发送测试邮件 |
    | POST | `/install` | 上传安装包 |

    `job_id` 用与 Agent 相同的正则校验。请求体接受任意 JSON，与 private-key 路由一样，避免校验错误把秘密回显出来。

- [ ] **Step 1：写失败测试**（对 `open_remote` 做 monkeypatch，返回一个假的 channel；参照 `test_remote_probe.py` 的写法）：
  - `test_call_sends_one_json_line_and_parses_result`
  - `test_maps_exit_codes_127_3_4`
  - `test_maps_agent_errors_conflict_409_invalid_422`
  - `test_output_over_2mib_fails`
  - `test_non_utf8_output_is_protocol_error_not_500`
  - `test_routes_require_owner`
  - `test_secret_fields_not_echoed_on_validation_error`
  - `test_upload_bundle_excludes_tests_and_quotes_path`
- [ ] **Step 2–4：** 运行 `cd backend && <venv>/python -m pytest tests/test_agent_client.py tests/test_agent_api.py -q`：先失败 → 实现 → 通过。然后运行全部后端测试，确认没有回归。
- [ ] **Step 5：提交** `feat: reach the server agent through pinned SSH`

### Task 10：前端「服务器 Agent」页面：状态、作业与历史

**Files：**
- Create：
  - `frontend/src/composables/useAgentApi.js`
  - `frontend/src/views/dashboard/Agent.vue`
  - `frontend/src/components/agent/JobList.vue`、`JobDetail.vue`、`RunOutput.vue`
  - `frontend/tests/agent.test.js`
- Modify：
  - `frontend/src/router/index.js`：添加 `path: 'agent'`，`name: 'Agent'`
  - `frontend/src/views/DashboardLayout.vue`：在侧栏中「任务工作区」之后添加「服务器 Agent」

**Interfaces：**
- `useAgentApi.js` 导出 `agentApi`，其中的方法与 Task 9 的路由一一对应：`status(id, signal)`、`jobs`、`job`、`createJob`、`updateJob`、`deleteJob`、`run`、`pause`、`resume`、`runs`、`output`、`cancel`、`preview`、`alerts`、`saveAlerts`、`testEmail`、`install`。请求方式沿用 `useWorkspaceApi.js` 中的 `request`。
- `Agent.vue`：
  - 选择服务器时只列出已固定指纹的服务器，数据源与 Monitor 的 `/api/monitor/hosts` 相同（去掉 local）。
  - 根据 status 的结果分几种显示：`agent_not_installed` 显示 `AgentInstall`；`agent_permission` 和 `agent_unreachable` 显示中文说明和处理办法；正常时显示状态条、触发中的告警（置顶）和 `JobList`。
- `JobList`：
  - 列：名称、计划（cron 与时区）、下次运行、上次结果（状态徽标）、上次耗时、暂停标记；
  - 点击一行打开 `JobDetail`。
- `JobDetail`：
  - 运行历史表，可按状态筛选，可以“加载更早的记录”；
  - 按钮：立即运行、暂停 / 恢复、编辑、删除、取消正在运行。删除和取消需要二次确认。
- `RunOutput`：
  - 按偏移量分段读取输出；
  - 运行中时可以开启“跟随”，每 2 秒拉取一次，沿用现有观测面板的顺延做法。设计中写的是 ctl 逐行流式输出，这里改为按偏移量轮询，是计划阶段的决定：这样更简单，也与已有的日志跟随一致；
  - 截断时显示提示。
- 中文状态名：
  - running 运行中、succeeded 成功、failed 失败、timed_out 超时、skipped_overlap 跳过（上次未结束）、missed 错过、agent_lost Agent 中断、cancelled 已取消；
  - trigger 列：schedule 按计划、manual 手动、retry 重试、catch_up 补跑（迟到 N 分钟）。
- 异步保护：切换服务器或作业时，用代次计数加 AbortController 丢弃迟到的响应，做法与 `ConnectionPanel.vue` 相同。

- [ ] **Step 1：写失败测试**：
  - `shows install guide when agent not installed`
  - `explains permission error with relogin hint`
  - `lists jobs with next run and last status in Chinese`
  - `firing alerts are shown above jobs`
  - `run now / pause / resume call the API and refresh`
  - `delete needs confirmation`
  - `late reply from previous server is ignored`
  - `output follow stops when run finishes and shows truncated notice`
- [ ] **Step 2–4：** 运行 `cd frontend && npx vitest run tests/agent.test.js`：先失败 → 实现 → 通过。
- [ ] **Step 5：提交** `feat: show server agent jobs and run history`

### Task 11：前端表单：作业编辑、告警设置与安装

**Files：**
- Create：`frontend/src/components/agent/JobForm.vue`、`AlertSettings.vue`、`AgentInstall.vue`、`frontend/tests/agent-forms.test.js`

**Interfaces：**
- `JobForm`，props 为 `{serverId, job?}`，emits `saved` 和 `cancel`：
  - 命令输入：每个参数一行，提交时组成 argv 数组；在旁边说明“不经过 shell；需要管道等请写 /bin/sh -c”。
  - 计划：可以添加多个 cron 表达式，选择时区（默认取 status 返回的 timezone）。输入停顿 400 毫秒后调用 preview，显示接下来 3 次运行时间；预览出错时显示错误，并禁止保存。
  - 其他字段：超时、重试次数、重试间隔、补跑窗口、告警开关（失败、恢复、附带输出）、`env`（明文键值对）、`secret_env`。每个秘密键提供“保持不变 / 改为新值 / 删除”三种选择，新值用 password 类型输入框。
  - 保存时带上 `revision`。遇到 409 时显示“该作业已在别处修改，请刷新后再改”，并保留表单内容。
  - 界面要说明：作业以服务器上的 `panellab` 用户运行，能定义作业就等于能在该服务器上执行命令。
- `AlertSettings`：
  - SMTP 设置：主机、端口、安全方式（STARTTLS、SSL、无），以及用户名、密码（只写，同样提供保持、修改、删除）、发件人、收件人（多个）。
  - 阈值：磁盘、CPU、内存的百分比，持续分钟数，SSH 失败次数。
  - 心跳 URL：只接受 https。
  - SSH 告警开关；“发送测试邮件”按钮，并显示最近一次发信结果。
- `AgentInstall`：
  - 说明 Agent 是什么、装在哪里、会创建 `panellab` 用户。
  - 「上传安装包」按钮调用 install 接口，然后显示需要执行的 sudo 命令，提供复制按钮和「在终端打开」（复用 TaskDetail 中的终端会话做法，初始目录设为上传目录）。
  - 安装后提示重新连接，并提供「重新检查」按钮。

- [ ] **Step 1：写失败测试**：
  - `command lines become argv array`
  - `invalid cron disables save and shows error from preview`
  - `stale revision shows conflict and keeps input`
  - `secret env unchanged sends no value; delete sends null`
  - `smtp password never prefilled`
  - `heartbeat rejects http url`
  - `install shows sudo command after upload`
- [ ] **Step 2–4：** 运行失败 → 实现 → 运行通过，然后运行全部前端测试，再执行 `npx vite build`。
- [ ] **Step 5：提交** `feat: edit agent jobs, alert settings and install from the panel`

### Task 12：端到端验证、文档与收尾

**Files：**
- Modify：`scripts/verify_agent_lab.py`，增加 `--panel` 模式，在 systemd 夹具上通过 PanelLab 后端 API 跑完整条路径。
- Create：`docs/user/server-agent.md`、`docs/project/server-agent-<日期>.md`
- Modify：
  - `docs/user/user-manual.md`：添加入口链接
  - `docs/project/feature-roadmap.md`：在接手后重排中加入第 6 项“服务器 Agent 第一版”
  - `/Users/meilingluo/Developer/Projects/PanelLab/NAVIGATION.md`：只把“当前记录”改为新记录；按 AGENTS.md 的要求，写入前先重读

- [ ] **Step 1：** 运行 `--panel` 模式：固定指纹 → 上传安装包 → 在夹具中执行 sudo 安装 → 在页面 API 中依次完成：创建作业、立即运行、读取输出、失败告警邮件、恢复邮件、暂停与恢复、修改告警阈值、发送测试邮件。预期输出 `agent panel lab: all checks passed`。
- [ ] **Step 2：** 用本地启动器在浏览器中走一遍，截图保存到 `docs/project/assets/`。
- [ ] **Step 3：** 编写用户文档，内容包括：
  - 安装与卸载；
  - 定义作业的写法，以旧 DailyNews 为例（argv 写法、`env_file` 或 `secret_env`，以及“先准备后发送”的两个作业）；
  - 告警的含义和心跳服务的配置方法；
  - 与旧 systemd 定时器并存时的注意事项：不要让两边同时发信。
- [ ] **Step 4：** 编写记录：实现内容、自动化验证、真实协议验证、未验证项、独立审查结论，以及“用户在阿里云上的安装验收”待办。
- [ ] **Step 5：** 运行全部测试：agent、后端、前端，以及前端构建。
- [ ] **Step 6：提交** `docs: record the server agent first version`
