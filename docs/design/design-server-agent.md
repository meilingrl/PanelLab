# 服务器 Agent：本机控制台 + 服务器常驻代理（第一版）

2026-11-10。分支 `feat/server-agent`，基线 `c8cb020`（PR #2 合并后的 main）。本文的“Agent”指装在服务器上的 PanelLab 常驻进程，**不是** MCP 接入的外部 AI Agent。

## 意图

用户在阿里云上有一个每日新闻简报（DailyNews），用两个 systemd 用户定时器运行。它能工作，但难以照看，也难以调整。用户要做一个新版简报，并且要求 PanelLab 不为某个程序专门定制，而是用通用方式控制和监控服务器上的软件。

用户确认的架构（原话要点）：
- 主界面留在 Mac 上，作为集中的控制面板和仪表盘。
- 每台服务器装一个轻量的 PanelLab Agent。它负责 24 小时调度，并在 Mac 睡眠时自己发邮件告警。
- Mac 是遥控器，重活由服务器做。

用户确认的顺序：先做 Agent 核心（调度、历史、告警、Mac 控制）；然后做设置表单；最后在其上做新版 DailyNews。旧 DailyNews 在新版证明可靠前不动。

## 目标（第一版）

1. **调度**：Agent 按时间表运行用户定义的作业。Mac 关机或睡眠时照常运行。
2. **历史**：每次运行都有记录，包括开始和结束时间、结果、退出码、触发方式和完整输出。
3. **告警邮件**：Agent 直接给用户发邮件。用户点名要的情形：
   - 作业失败、超时、错过运行；
   - 磁盘使用超过 90%；
   - CPU 或内存持续超过 95% 达 10 分钟；
   - 服务器不可达或断网（经外部心跳服务）；
   - SSH 暴力登录（用户标为可选）。
4. **Mac 控制**：在 PanelLab 中可以：
   - 查看每台服务器的 Agent 状态、作业、历史和日志；
   - 新建、编辑和删除作业；
   - 立即运行、暂停和恢复作业；
   - 配置告警和阈值。
5. **通用**：Agent 与 PanelLab 中没有任何新闻专用代码。DailyNews 只是一个普通作业。

## 非目标（第一版不做）

- 设置表单。作业带一份设置说明，PanelLab 据此渲染表单，这是第二阶段。第一版只需为它保留扩展余地。
- 新版 DailyNews（第三阶段）、迁移或改动旧 DailyNews。
- 邮件以外的通知渠道。
- Agent 自动升级。
- 服务器上的 Web 界面。
- 多服务器批量操作。
- 非 Linux 服务器。
- 让 MCP 工具获得作业写权限。MCP 保持只读，最多读取作业状态，这一项也可以以后再做。

## 反目标（即使能用也算失败）

- Agent 在公网或局域网开放任何端口。
- Agent 成为 root 后门：它以普通用户运行，任何拿到 Mac 以外入口的人都不能借它提权。
- 告警刷屏：同一问题持续时反复发信，或抖动时来回发信。
- PanelLab 掉线，或 Mac 睡眠，导致作业不跑或告警不发。
- 静默失败：作业没跑、Agent 挂了或邮件发不出时没有任何痕迹。
- 影响服务器上现有的服务：旧 DailyNews、nginx、xray、fail2ban、MySQL 容器等。

## 环境事实（来自用户服务器，2026-11-10）

- 系统为 Debian 系，有 `python3` 和 systemd。
- 用户 `admin` 有 sudo，需要密码；PanelLab 现在以 `admin` 经固定指纹的 SSH 登录。
- 其他账户有 `agent`、`deploy`、`newsbot`、`ykx`。其中 `agent` 账户已存在但用途未知，**不要占用或改动它**。
- 已运行 fail2ban、Tailscale、nginx、阿里云备份客户端，以及 Docker 中的 MySQL（127.0.0.1:3306）。
- 旧 DailyNews 的结构：
  - 账户 `newsbot` 下有两个用户定时器：23:20、23:40、23:50 准备，00:00 发送（Asia/Shanghai）；
  - 代码在 `/srv/news-briefing`，配置为 `~newsbot/.config/news-briefing/env`；
  - 日志进 journal。

## 架构

```
Mac: PanelLab（Vue + FastAPI，本地启动器）
  │  已有：固定指纹的 SSH（paramiko），以 admin 登录
  │  新增：通过 SSH 执行 `panellab-agent ctl`，请求和响应走 stdin/stdout 的 JSON
  ▼
服务器: panellab-agentctl ──Unix socket（仅 panellab 组可连）──▶ panellab-agent 守护进程
                                                       ├─ 调度器 + 运行器 → 作业子进程
                                                       ├─ SQLite：作业、运行、告警状态
                                                       ├─ 每次运行的输出文件
                                                       ├─ 资源 / SSH 登录检查器
                                                       ├─ 心跳 ping（可选，HTTPS 出站）
                                                       └─ SMTP 发信（出站）
```

## Agent 设计

### 运行身份与位置（我的决定）

- 创建系统用户和组 `panellab`（无登录 shell），守护进程以它运行。**作业也以 `panellab` 运行**，不切换到其他用户。这样 Agent 不需要 root，也就不可能成为提权入口。新版 DailyNews 会部署在这个用户下。
- 为了读 sshd 失败登录，`panellab` 加入 `adm` 组（只读 journal 和认证日志）。
- 程序位于 `/opt/panellab-agent/<版本>/`，并有指向当前版本的链接。
- 数据位于 `/var/lib/panellab-agent/`：SQLite、运行输出、作业工作目录的默认父目录。
- 配置为 `/etc/panellab-agent/agent.json`，SMTP 密码存在单独的 `secrets.json`，权限 0600，属主 `panellab`。
- 用 systemd 系统服务 `panellab-agent.service` 托管，设置 `Restart=always`。
- **作业目录（我的决定）**：
  - 作业的 `workdir` 可以是 `panellab` 有权写入的任意路径，例如 `/srv/<程序>`，不限于 `/var/lib/panellab-agent`。
  - 加固项只用不限制路径的几种：`NoNewPrivileges=yes`、`ProtectSystem=full`（只把 `/usr`、`/boot`、`/etc` 设为只读，`/srv`、`/var` 仍可写），以及 `PrivateTmp=yes`。
  - 不用 `ProtectSystem=strict` 或 `ProtectHome`，因为它们会让作业无法写自己的程序目录。
  - 应用作业时，`/etc/panellab-agent` 由 Agent 的 Python 代码自己写入；单元中把该目录列入 `ReadWritePaths`。
- **重启与正在运行的作业（我的决定）**：
  - 作业是守护进程的子进程，与它同属一个 cgroup。
  - 意外崩溃时 systemd 会同时结束正在运行的作业。这类运行一律标为 `agent_lost` 并告警，不尝试让作业在守护进程重启后继续存活。
  - 计划内的重启和升级，先“排空”：`install.sh` 先调用 `panellab-agentctl drain`，Agent 不再启动新运行，等正在运行的作业结束（最多等其各自的超时时间，可加 `--force` 跳过），然后才重启。
  - 排空是一个 30 秒的租约（R23，最终审查后补充）：`drain` 设定租约，`ctl drain` 在等待期间每次轮询（不超过 10 秒）重发以续期；无人续期时 Agent 自动退出排空、写日志并发通知“Agent 已退出排空状态”。另有 `undrain` 操作和 `panellab-agentctl undrain`，`install.sh` 在请求过排空后失败时由 EXIT trap 调用。排空期间 `last_scheduled` 不前进，退出排空后按正常的补跑窗口和错过规则处理到期的运行。
  - `systemctl stop` / `restart` / 关机（R22，最终审查后补充）：单元使用 `KillMode=mixed`、`TimeoutStopSec=150`，SIGTERM 只发给守护进程。守护进程停止调度，给正在运行的作业最多 60 秒自行结束，之后经运行器取消剩下的（进程组 SIGTERM，30 秒后 SIGKILL），记为 `cancelled`，输出末尾加一行 `[Agent 停止，作业被中断]`，不发失败告警，只发一封“Agent 停止时中断了作业”列出作业名和运行编号；随后在 20 秒内清空发信队列并退出。守护进程退出后 systemd 对 cgroup 中剩余进程发 SIGKILL。
- **Agent 停机告警**：Agent 启动时，若发现上次运行留下的最后心跳与现在相隔超过 5 分钟，就发一封“Agent 曾停止运行：从 X 到 Y”。这样补跑和错过都有迹可查。
- **只用 Python 标准库**（sqlite3、smtplib、json、socket 等），服务器无需 pip。cron 表达式自行实现标准 5 字段解析，含时区。要求 Python ≥ 3.9（需要 `zoneinfo`）。

### 作业定义

作业存放在服务器上的 SQLite，以 Agent 为唯一真相；PanelLab 不保存副本，只做显示缓存。字段：

- `id`：短标识，例如 `news-briefing`；`name`：显示名。
- `command`：argv 数组，**不经 shell**。需要 shell 时用户显式写 `["/bin/sh","-c","…"]`。
- `workdir`：工作目录；`env`：键值对；`env_file`：可选路径。
- `secret_env`（我的决定）：键值对，**只写不读**，处理方式同 SMTP 密码：
  - 存在服务器上权限为 0600 的 `secrets.json` 中，不存入 SQLite；
  - `jobs.get` 只返回键名，并标明“已设置”；
  - 编辑表单对每个键提供“保持不变 / 改为新值 / 删除”三种操作；
  - PanelLab 不缓存这些值；
  - 运行作业时，把它们与 `env` 合并后注入。

  普通 `env` 可以明文显示，界面要提示：API 密钥等请放在 `secret_env` 中。
- `schedule`：一个或多个 cron 表达式，加时区，默认取服务器时区。允许为空，即只能手动运行。
- `timeout_seconds`：默认 1 小时。
- `retries` 与 `retry_delay_seconds`：失败后重试的次数和间隔。
- `catch_up_window_minutes`（我的决定）：Agent 停机期间错过的运行，在计划时间之后多久以内可以补跑。默认 60 分钟；设为 0 表示从不补跑。
  - 恢复时，在窗口内的错过运行**合并成一次**补跑，触发方式记为 `catch_up`，并在运行记录和告警邮件里注明“迟到 N 分钟”。
  - 超出窗口的运行，每个计划时间点都记一条 `missed`，并按作业告警发信，不再补跑。
  - 理由：每天 00:00 的发信作业如果到早上 9 点才补发，内容已经过时，不如明确告诉用户“没发出去”。旧 DailyNews 的 `Persistent=true` 不限迟到时间，新设计有意改变了这一点。
- `overlap`：上一次还在运行时又到点，固定为跳过并记录，第一版不提供排队。
- `paused`：布尔值。
- `alert`：失败、超时、错过时是否发信（默认是），以及“恢复正常”时是否发信（默认是）。
- `settings_schema` / `settings_file`：**第二阶段使用**，第一版只保留字段，不解释。
- `created_at`、`updated_at`、`revision`：`revision` 用于防止两处同时编辑互相覆盖。

### 运行与历史

- 每次运行记录以下内容：
  - `run_id`、`job_id`、`trigger`（schedule / manual / retry / catch_up）、`scheduled_for`；
  - `started_at`、`ended_at`；
  - `status`（running / succeeded / failed / timed_out / skipped_overlap / missed / agent_lost）；
  - `exit_code`、`attempt`、输出文件路径和输出字节数。
- stdout 与 stderr 合并写入每次运行各自的文件，单次输出上限 10 MiB，超出截断并标注。
- 保留期：每个作业保留最近 200 次运行或 90 天，取较多者，超出部分清理。
- 超时处理：先向整个进程组发 SIGTERM，30 秒后发 SIGKILL。
- 守护进程重启时，凡是 `running` 状态但进程已不存在的运行，一律标为 `agent_lost` 并告警。不猜测它们成功了。

### 告警

- **告警状态机**：每个告警键有“正常 / 触发”两种状态。告警键的例子：`job:news-briefing`、`disk:/`、`cpu`、`mem`、`ssh`。状态从正常变为触发时发一封信；恢复正常时发一封“已恢复”。状态不变则不重复发。持续触发超过 24 小时，每天最多再提醒一次。
- 作业类：
  - 最后一次尝试（重试用尽后）失败或超时，触发；之后一次成功，恢复。
  - 计划时间过去 15 分钟仍未开始（例如 Agent 停机后又未补跑），记为 `missed`。
- 资源类（阈值可在 PanelLab 中修改）：
  - 每个真实文件系统的使用率超过 90% 时触发，回落到 85% 以下时恢复。排除 tmpfs、overlay、squashfs 等伪文件系统。触发中的挂载点从挂载列表中消失时也算恢复。
  - CPU：每 30 秒从 `/proc/stat` 采样一次，连续 10 分钟每次都超过 95% 时触发，连续 5 分钟低于 90% 时恢复。
  - 实际恢复线（R21，最终审查后补充）= min（配置的恢复线或默认值，触发线 − 5），磁盘对应 `disk_recover_percent`，CPU / 内存对应 `recover_below_percent`，避免调低触发线后在阈值附近反复触发和恢复。`alerts.configure` 拒绝（`invalid`）显式设置的、不低于对应触发线的恢复线。
  - Agent 启动时，`cpu`、`mem`、`disk:<挂载点>` 的触发状态从已保存的告警恢复，之后照常判断是否恢复。
  - 恢复邮件只在触发时确实发过信的情况下发送。
  - 内存：计算方式同 CPU，使用率 = 1 − MemAvailable/MemTotal。
- SSH 登录类（用户标为可选，放在第一版最后一步，时间不够可推迟）：
  - 统计 10 分钟内 sshd 认证失败次数，超过 30 次触发；信中列出次数最多的来源 IP，最多 5 个。
  - 数据从 journal 读取（sshd 单元，或 `/var/log/auth.log`）。
  - 服务器已有 fail2ban 封禁，这里**只告警，不封禁**。
- 外部心跳（可选）：
  - 用户填入一个心跳服务的 ping URL（例如 healthchecks.io 或同类服务），Agent 每 5 分钟用 HTTPS GET 访问一次。
  - 服务器宕机或断网时，由该外部服务给用户发信。这是唯一能覆盖“整台服务器不可达”的手段。
  - 不填 URL 就不发出任何请求。
- 邮件：
  - 通过 SMTP 发送，支持 STARTTLS 和 SSL，配置项为主机、端口、用户名、密码、发件人、收件人列表。
  - PanelLab 中有“发送测试邮件”。
  - 发送失败时记录在 Agent 状态里，并在 PanelLab 显示。不能静默。
  - 信件内容为简短的中文纯文本：发生了什么、在哪台服务器、什么时间，以及最近 20 行输出（仅限作业类）。

### 控制通道（我的决定）

- 守护进程只在 `/run/panellab-agent/agent.sock` 监听，权限为 0660，组 `panellab`。不开 TCP 端口。
- 用户把 `admin` 加入 `panellab` 组。PanelLab 用已有的固定指纹 SSH 连接执行 `/usr/local/bin/panellab-agentctl`，经 stdin 发送一条 JSON 请求，再从 stdout 读一条 JSON 响应。跟随日志时改为逐行输出。
- 通道的权限边界就是“能以 panellab 组成员身份登录这台服务器”，不另设令牌。
- 协议带 `protocol_version`。PanelLab 遇到不兼容的版本时，提示用户升级 Agent，不去猜。
- 操作：
  - 读取类：`status`（Agent 版本、启动时间、告警配置摘要、当前触发的告警、最后一次发信结果、最后一次心跳结果 `heartbeat: {configured, at, ok, error}`）、`jobs.list`、`jobs.get`、`runs.list`、`runs.output`（支持偏移量和跟随）。
  - 写入类：`jobs.create`、`jobs.update`（需带 revision）、`jobs.delete`、`jobs.run_now`、`jobs.pause`、`jobs.resume`、`runs.cancel`、`alerts.configure`、`alerts.test_email`、`drain`、`undrain`。
  - 删除作业（R24，最终审查后补充）：作业记录改名为 `<id>~<rowid>` 留作墓碑，它的运行在同一事务中跟着改名，原 ID 可以重新创建；旧运行仍可按运行编号读取，清理规则同样覆盖已删除作业的运行和输出文件。
- 秘密不出服务器：`status` 和 `alerts` 的读取结果只返回“已设置密码”，不返回密码本身。

## PanelLab（Mac）侧

- 新增页面「服务器 Agent」，从侧边栏进入，按服务器分组：
  - 未安装：显示安装说明。
  - 已安装：显示版本和状态、作业列表，以及每个作业的下次运行、上次结果和最近一次耗时。
  - 当前触发的告警置顶显示。
- 作业详情：
  - 运行历史表，可按状态筛选；
  - 点开一次运行可以查看输出，正在运行的可以跟随；
  - 按钮：立即运行、暂停 / 恢复、编辑、删除、取消正在运行。删除和取消需要二次确认。
- 作业编辑表单覆盖“作业定义”的全部字段。cron 字段旁显示“接下来 3 次运行时间”，这些时间由 Agent 计算后返回，避免 Mac 和服务器的时区算法不一致。
- 告警设置页：SMTP、收件人、阈值、心跳 URL、SSH 告警开关、测试邮件。
- 后端在现有远程连接服务之上新增 `services/agent_client.py` 和路由 `/api/servers/{id}/agent/...`，复用 `open_remote` 与固定指纹，权限沿用服务器所有者。
- 现有「任务工作区」中 `scheduled` 类登记保持不变。是否把 Agent 作业也显示在工作区里，由实现者决定；不要求第一版合并两者。

## 安装、升级、卸载

- 仓库新增 `agent/` 目录，放守护进程、`panellab-agentctl`、systemd 单元和 `install.sh`。
- PanelLab 的「安装 / 升级 Agent」按钮：
  1. 经现有 SFTP 把带版本号的安装包上传到 `admin` 的家目录 `~/panellab-agent-<版本>/`；
  2. 显示一条需要用户在终端中自己执行的 `sudo bash …/install.sh` 命令，可在「在此目录打开终端」中直接粘贴执行。
- PanelLab **从不**代用户输入 sudo 密码，也不保存它。
- `install.sh` 必须幂等，负责以下事项：
  - 创建 `panellab` 用户和组；
  - 把 `panellab` 加入 `adm` 组，把执行安装的用户加入 `panellab` 组；
  - 安装文件、写入单元、启动服务；
  - 升级时保留 `/var/lib` 和 `/etc` 中的数据。
- 安装完成后，提示用户需要重新登录 SSH，新的组权限才生效（PanelLab 新建连接时会自然生效）。
- `install.sh --uninstall`：停止服务、删除程序和单元。默认保留数据和配置，加 `--purge` 才删除。

## 安全

- 作业命令等同于以 `panellab` 用户执行任意命令。只有能经固定指纹 SSH 登录、并且属于 `panellab` 组的人才能定义作业。这与“能 SSH 就能执行命令”的现状等价，没有扩大权限。界面文案要说清这一点。
- `panellab` 不在 sudoers 中。
- MCP 工具不获得作业写入能力。
- SMTP 密码只存在服务器上，不回传到 Mac。在 Mac 上输入后直接经 SSH 写到服务器，不落入 PanelLab 数据库。
- 告警邮件中的作业输出可能含敏感信息。默认附带最近 20 行，可以按作业关闭。

## 验证

- Agent 单元测试：
  - cron 解析，包括时区、夏令时和闰日；
  - 调度决策，包括补跑、错过和重叠；
  - 运行器，包括超时时杀整个进程组、输出截断；
  - 告警状态机，包括去抖和每日提醒；
  - 资源阈值的持续时间判定；
  - SSH 失败计数；
  - 协议的错误处理。
- 时间相关测试使用可注入的时钟，不真正等待 10 分钟。
- PanelLab 后端与前端：与现有测试同样覆盖，包括请求过期、切换服务器、错误结构。
- 真实协议验证（我的决定，分两层）：
  1. **带 systemd 的 Debian 夹具**：新建镜像，以 systemd 为 PID 1，内含 sshd。用户模型模仿用户服务器：有 sudo 的 `admin` 用户，并有 `adm` 组。
     - 在 Colima 上以特权容器运行。
     - 用 `install.sh` 真实安装，验证：单元文件、加固项、`Restart=always`、socket 权限、组成员关系、排空后升级、卸载。
     - 然后由 PanelLab 经固定指纹 SSH 完成整条路径：定义作业 → 立即运行 → 查看输出 → 失败告警 → 恢复邮件。
     - 邮件发往夹具内的本地 SMTP 接收器，例如 Python 写的调试 SMTP 服务。
     - sshd 认证失败告警在这里用真实的失败登录验证。
  2. 如果 Colima 上无法运行 systemd 容器，退回到守护进程的 `--foreground` 模式，在现有的无 systemd 夹具中验证协议、调度和告警。systemd 相关各项在记录中明确标为“未验证”，等用户在真实服务器上安装时再验。
- 不在用户的阿里云服务器上测试。安装到真实服务器由用户自己执行，并单独记录为“用户验收”。

## 后续阶段（不属于第一版）

1. 设置表单：作业声明 `settings_schema`（JSON Schema 子集），PanelLab 渲染表单并写入 `settings_file`，带版本和回滚。
2. 新版 DailyNews：独立程序，作为 Agent 作业运行；包括去重、主题与版式、可调的类别和领域。
3. 可能的扩展：HTTP 健康检查作业类型、更多通知渠道、MCP 读取作业状态。
