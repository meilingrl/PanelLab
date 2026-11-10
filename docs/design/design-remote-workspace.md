# 远程任务工作台：人与 Agent 共用的任务现场

2026-11-03。用户明确授权睡前自主迭代，主控据已讨论的产品方向确定本轮边界，无需逐步等待确认。本轮从 aaaba62 开始，复用隔离工作区，分支 feat/remote-workspace；上一轮 feat/production-reliability 保留。

## 产品目标

用户主要照看训练实验、网站和定时任务，偶尔下载结果；不愿反复连接、记命令和手打目录。第一条可验收路径是：连接一台远程 Linux，点选正在运行的进程与目录，固定成有名字的任务，回来查看进程身份、最近日志、资源和结果文件，下载产物。CLI 与界面共用同一对象、API、权限及错误结构；Agent 登记的任务在 UI 可见。

本轮交付是本地运行的 Web 工作台及 CLI，复用现有 Vue/FastAPI；原生桌面包装暂不作为完成条件。训练优先，网站/定时任务共用登记、文件、日志、人工或 Agent 上报的执行记录。不会把登记定时任务说成安装了调度器，不会把进程存在说成训练有进展，不从零散日志猜测 loss、epoch 或任务成功。

## Tailscale 关系

Tailscale 提供网络和身份基础设施，PanelLab 提供任务语义、现场和操作界面。允许普通 SSH 使用 tailnet 地址；可显式选择 Tailscale SSH 的 none 认证，但仍固定主机公钥。可选读取本机 tailscale status --json 列出候选设备，不登录、不改 tailnet 策略、不增加网络授权。未安装或未运行时优雅说明。实际 tailnet 未授权测试，不宣称实网集成已验收。

官方核验来源（2026-11-03）：
- https://tailscale.com/docs/features/tailscale-ssh
- https://tailscale.com/docs/reference/tailscale-cli
- https://tailscale.com/docs/reference/tailscale-api

进一步核验发现 Tailscale Aperture 已提供连接机器后让 Agent 执行命令的 Chat 体验及 MCP 代理，因此 AI 运维入口已有实质重合。PanelLab 的候选差异在于无需聊天即可直接操作的训练/任务工作现场及跨 Agent 的持久对象；这是待试用验证的产品假设，不宣称独有。来源：https://tailscale.com/docs/aperture/what-is-aperture 与 https://tailscale.com/docs/aperture/mcp-server 。本轮不建设 LLM 网关或另一套聊天 Agent。

借鉴：同一核心提供 GUI 与机器接口；稳定 ID、结构化状态、明确的权限边界。Agent 与人使用同一账号权限，本轮不宣称细粒度 Agent 委派权限。

## 连接与观察

复用 Server 表，新建绑定 server_id 的连接信任表，不静默修改既有表。新连接流程：探测 SSH 公钥指纹 → 显示 host/port/算法/指纹并由用户核实 → 确认短期签名凭证固定公钥 → 认证。公钥或连接目标变化时拒绝，必须重新核实。支持现有密码与显式 tailscale none 模式；不隐式使用后台宿主机的 SSH agent/私钥。标准私钥配置是后续选项。

统一远程连接服务供任务观察、SFTP和现有终端使用。终端替换 URL 中的长期 JWT 为短期、单次票据，票据绑定用户、服务器和可选初始目录；服务器确认并安全 quote 后设置初始目录。既有独立监控页面保留为旧入口，不作为工作台数据源。

远端只要求 Linux/Python3 和 SSH/SFTP；采集用固定只读脚本，无远端安装和写入。执行时间、输出、文件和目录规模都有上限。CPU/内存/网络来自 /proc；GPU 通过可用的 nvidia-smi 读取，缺失时显示不可用。进程选择保存 PID + boot_id + start_ticks，防止重用 PID 误绑定。

## 任务及文件

Workload 包含 owner、server_id、name、kind(training/website/scheduled)、root_path、log_path（可空）、pid和process_identity（可空）、website_url（可空）、schedule_note（可空）、notes、时间。日志必须位于任务根目录内。CRUD只改面板元数据，不杀进程、不删远程文件、不安装 cron。

工作台提供服务器级目录/进程选择器，创建任务后浏览限制在任务根目录。拒绝特殊文件和符号链接跳转，采用 SFTP normalize/lstat 和根路径校验；远端并发修改不保证快照原子性。文件预览和尾部日志有界；下载上限16MiB，较大产物明确提示使用终端/专用传输工具。文件名支持空格/中文/引号，绝不能变成命令片段。

每次观察返回 observed_at、连接结果和各模块可用性；网络断开不会把旧值伪装为实时。绑定进程身份匹配才显示 running；未绑定显示 unbound；进程消失显示 exited_or_missing，不能推断成功失败。网站 URL 仅作为打开入口，未做 HTTP 健康验收。运行记录由人或 CLI/Agent 显式上报，source=reported，UI突出显示“上报记录”，不冒充采集证据。

## 体验

新增 /workspaces 作为登录后主入口。任务列表按服务器/类型筛选；点开显示概览、日志、文件、执行记录。目录选择器可导航，点选日志和任务根目录；进程选择展示命令摘要、PID及资源，支持不绑定进程。详情可在当前目录打开终端、下载文件、复制任务ID和 CLI 示例。日志按文本渲染；切换目标丢弃迟到响应；失败保留历史但明确过期。小屏正常操作，基本键盘/标签可用。

## 验收及限制

有行为测试、独立审查、隔离真实 SSH/SFTP 的端到端验证、浏览器实际点选及 CLI 共用数据验证。无真实服务器、实验、生产数据库或 tailnet 修改。既有站点变更保护不退化。本轮不做任意远程执行 API、自动重启、任务调度或训练指标推断。后续以真实试用反馈决定启动/调度管理、完整传输和原生壳的优先顺序。
