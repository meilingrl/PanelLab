# 服务器 Agent 第一版

基线为 `c8cb020`（PR #2 合并后的 main），开发分支为 `feat/server-agent`。依据见[路线图接手重排](feature-roadmap.md#接手后重排2026-11-10)第 6 项、[设计](../design/design-server-agent.md)和[实施计划](../design/plan-server-agent.md)，使用方法见[用户指南](../user/server-agent.md)。用户要求 PanelLab 用通用方式控制服务器上的软件，而不是为 DailyNews 单独定制；本轮只做 Agent 核心，设置表单和新版 DailyNews 留给后续阶段，旧 DailyNews 未改动。

## 实现内容

| 层 | 内容 |
| --- | --- |
| Agent（`agent/panellab_agent/`，仅标准库，兼容 Python 3.9） | 5 字段 cron 与时区；作业、运行、告警状态与秘密分开存放（SQLite 与 0600 的 `secrets.json`）；调度决策（按时、补跑窗口内合并补跑、超窗记 `missed`、重叠跳过）；运行器（argv 不经 shell、进程组超时 SIGTERM→SIGKILL、10 MiB 输出上限、失败和超时重试）；告警状态机（触发与恢复各一封、24 小时提醒）、SMTP 发信队列；磁盘、CPU、内存、SSH 登录失败检查与心跳；Unix socket 上的 JSON 控制协议与 `panellab-agentctl`；启动时把中断的运行标为 `agent_lost`、报告停机时段 |
| 安装（`agent/install.sh`、`agent/systemd/`） | 创建 `panellab` 系统用户（无 shell、不在 sudoers、不动已有 `agent` 账户）；systemd 单元带 `Restart=always` 和不限路径的加固项；升级前排空、`--force`、`--uninstall [--purge]`；以 root 运行时不从当前目录导入代码 |
| 后端（`backend/services/agent_client.py`、`backend/routers/agent.py`） | 经固定指纹 SSH 执行 `panellab-agentctl`，`/api/servers/{id}/agent/...` 下的作业、运行、输出、告警和安装路由；错误统一映射为 `agent_not_installed`、`agent_permission`、`agent_unreachable` 等；安装包按内容校验码命名后经 SFTP 上传，只返回一条由用户自己运行的 sudo 命令 |
| 前端（「服务器 Agent」页面与 `components/agent/`） | 状态、触发中的告警、作业列表与详情、运行历史与输出跟随；作业表单（argv 每行一个参数、`env_file`、只写的秘密环境变量、由 Agent 计算的接下来 3 次运行时间）；告警设置与测试邮件；未安装和协议不兼容时的安装指引；状态栏显示最近一次告警邮件的发送失败或「尚未配置告警邮件」，已安装时提供「升级 / 重新安装 Agent」（`4d41a8d`） |
| 真实协议验证（`scripts/verify_agent_lab.py`、`scripts/fixtures/agent_systemd/`） | 以 systemd 为 PID 1 的 Debian 夹具，内含 sshd 和本地 SMTP 接收器；默认模式直接验证安装与守护进程，`--panel` 模式经 PanelLab 后端 API 走完整条路径 |

## 自动化验证

2026-11-11 在分支最新提交上重跑：

- Agent 单元测试 227 项通过（`cd agent && python -m pytest -q`）；安装脚本静态检查 22 项通过（`scripts/tests/test_agent_install_static.py`）。
- 后端 245 项通过；前端 13 个文件 203 项通过（含 `4d41a8d` 新增的发信结果与升级入口用例），生产构建通过。
- 时间相关用例使用可注入时钟，不真实等待。

## 真实协议验证

在本机 Colima 上运行，容器只有 `panellab-agent-lab`（夹具）和 `panellab-agent-lab-mysql`（`--panel` 的隔离后端数据库），结束后均已删除；未使用用户自己的 PanelLab 实例或任何真实服务器。

- `python3 scripts/verify_agent_lab.py`：54 项全部通过，结尾为 `agent lab: all checks passed`。覆盖 `install.sh` 真实安装、单元加固与重启属性、socket 与目录权限、组成员关系、非成员被拒、从恶意当前目录运行不导入其中代码、测试邮件、失败与恢复邮件、`kill -9` 后 systemd 重启并标记 `agent_lost`、升级时等待正在运行的作业、卸载与 `--purge`。
- `.venv311/bin/python scripts/verify_agent_lab.py --panel`：26 项全部通过，结尾为 `agent panel lab: all checks passed`。隔离后端只监听本机随机端口，夹具 sshd 只发布到 `127.0.0.1` 随机端口。路径为：探测指纹并与容器内 `ssh-keygen -lf` 核对 → 信任 → 未安装时返回 `agent_not_installed` → 上传安装包 → 在容器内以 `admin` 运行返回的那一条 sudo 命令 → 状态正常 → 配置 SMTP → 新建带秘密环境变量的作业 → 立即运行并读取输出（以 `panellab` 运行，秘密已注入）→ 诱发失败并在 SMTP 接收器收到失败邮件 → 成功并收到恢复邮件 → 暂停与恢复 → 修改磁盘阈值 → 测试邮件。全程检查 `GET /alerts`、`GET /jobs/{id}` 的响应和后端日志中不出现 SMTP 密码或秘密环境变量的值。
- 隔离后端使用临时 MySQL 而不是 SQLite：`servers` 等表的 BIGINT 主键在 SQLite 中不会自增，这是已有模型的限制，与本轮无关。

浏览器走查：待补充

## 独立审查

每个任务完成后都由实现者以外的审查者审查了对应提交（Agent 各模块、安装与夹具、后端、前端），审查中发现的问题已在各轮修复提交中处理并复审，例如：控制命令和安装脚本以 root 运行时不再从当前目录导入代码、安装目录按内容校验码命名、发信错误不带链式上下文、运行器在输出管道卡住时放弃并继续处理其他作业、作业表单补上 `env_file`。

最终整体审查：待补充

## 未验证

- 在用户阿里云服务器上的真实安装：属于用户验收，见下文“下一步”。
- Tailscale 地址下的连接；真实 GPU 与本功能无关。
- SSH 暴力登录告警只在单元测试中用模拟的日志行验证，夹具中没有用真实的大量失败登录触发。
- STARTTLS 和 SSL 发信路径没有实际执行过：单元测试只对本地无加密接收器发信并校验安全方式取值，夹具同样使用无加密的本地接收器，也未连接真实邮件服务商。
- 心跳只在单元测试中验证，未连接真实心跳服务。

## 边界

- Agent 不开端口，只连 SMTP 和用户填写的心跳地址；作业以 `panellab` 运行，不能 sudo。能定义作业的人等于能在服务器上以 `panellab` 执行命令。
- PanelLab 不输入、不保存 sudo 密码；SMTP 密码和秘密环境变量只存在服务器上。
- MCP 工具没有作业写入能力。
- 夹具与 Mac 在同一台机器的 Docker 网络中，没有覆盖公网延迟、云厂商镜像与真实服务器上已有的 nginx、fail2ban、Docker 中的 MySQL 等服务。

## 下一步（用户）

1. 合并本分支后，用启动器打开 PanelLab，在「服务器 Agent」选中阿里云服务器，点「上传安装包」，在终端自己运行页面给出的 sudo 命令，重新连接 SSH 后点「重新检查」。
2. 在「告警设置」填好 SMTP 并发送测试邮件；建议同时配置一个心跳服务。
3. 按[用户指南](../user/server-agent.md#示例把旧-dailynews-迁到-agent)中的示例把旧 DailyNews 迁为两个作业。新作业至少成功一次后，再停用 `newsbot` 下对应的旧定时器，任何时候都不要让两边同时发信。
4. 记下安装和使用中卡住或不顺手的地方，作为验收结果和下一阶段（设置表单）的输入。
