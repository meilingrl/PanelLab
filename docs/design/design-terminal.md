# 网页端 SSH 终端 — 设计与实现总结

## 1. 目标与背景

在 PanelLab 的 Dashboard 中集成 Web 端 SSH 终端，使用户可直接在浏览器中对远程服务器进行 SSH 控制，无需在本地反复打开 Windows PowerShell 等终端工具，提升运维效率。

**实现状态**：已完成并集成到主流程。

---

## 2. 技术选型

| 层级 | 技术 | 说明 |
|------|------|------|
| 前端 UI | Vue 3 | 与现有面板一致 |
| 终端仿真 | @xterm/xterm | 在浏览器中渲染终端、处理键盘输入与 ANSI 输出 |
| 自适应 | @xterm/addon-fit | 窗口/容器尺寸变化时自动调整终端行列 |
| 通信 | 原生 WebSocket | 与后端保持长连接，双向传输终端 I/O |
| 后端 | FastAPI WebSocket | 处理 WS 连接、鉴权、与 SSH 桥接 |
| SSH 客户端 | paramiko | 建立 SSH 连接并调用 `invoke_shell()` 打开交互式 PTY |
| 配置与密码 | monitor_remote_config + utils.cipher | 复用系统监控中的远程主机配置与解密逻辑 |

---

## 3. 架构与数据流

### 3.1 连接建立

1. 用户登录后进入「终端控制」页，点击「连接」。
2. 前端从本地存储读取 JWT Token，建立 WebSocket：  
   `ws://<host>/api/ws/terminal?token=<jwt>`
3. 后端接受连接后：
   - 用 Query 中的 `token` 调用 `_decode_token` 校验 JWT，并查询用户；
   - 从数据库读取 **第一条** `monitor_remote_config`（与系统监控共用同一远程配置）；
   - 用 `decrypt_password` 解密密码，再使用 `paramiko.SSHClient` 连接远程主机并 `invoke_shell(term="xterm")`。

### 3.2 数据流转

- **前端 → 后端**：xterm 的 `onData` 将用户按键通过 WebSocket 以文本发送；后端收到后写入 SSH Channel。
- **后端 → 前端**：后台异步循环从 SSH Channel `recv()` 读数据，解码为 UTF-8 后通过 WebSocket 发给前端，由 xterm 渲染。
- **Resize**：前端用 ResizeObserver 监听容器尺寸变化，`FitAddon.fit()` 后向 WebSocket 发送 `{"type":"resize","cols":N,"rows":M}`；后端调用 `channel.resize_pty(width=N, height=M)`。

### 3.3 断开与资源释放

- 用户点击「断开」或关闭页面 → WebSocket 关闭；后端在 `ws_to_ssh` / `ssh_to_ws` 中捕获异常或循环结束，在 `finally` 中 `client.close()` 释放 paramiko 连接。

---

## 4. 接口与依赖

### 4.1 WebSocket 接口

- **路径**：`/api/ws/terminal`
- **查询参数**：`token`（必填），为登录后获得的 JWT。
- **鉴权**：未传或无效 token 时发送错误提示并 `close(1008)`；未配置远程主机或解密失败时同样关闭并提示。

### 4.2 配置来源

- 远程连接信息来自表 `monitor_remote_config`（与「系统监控」→「远程配置」共用）。
- 使用该表中**第一条**记录；若需多主机，可在后续版本中增加 `config_id` 等参数。

### 4.3 运行环境

- **后端**：Python 3.7+（推荐 3.8+）。SSH 连接在异步上下文中通过 `loop.run_in_executor(None, _do_connect)` 执行，避免阻塞事件循环，并兼容不提供 `asyncio.to_thread` 的 Python 3.8 及以下。
- **前端**：需支持 WebSocket 与 ES Module（Vite 构建）。开发时 Vite 代理需对 `/api` 开启 `ws: true` 以转发 WebSocket。

---

## 5. 实现总结（已完成的开发内容）

### 5.1 后端

- **文件**：`backend/routers/terminal.py`
  - WebSocket 路由 `/api/ws/terminal`，Query 鉴权，从 `MonitorRemoteConfig` 取连接信息并解密；
  - 使用 `paramiko` 建立 SSH、`invoke_shell()`、双任务：`ws_to_ssh`（收 WebSocket 写 SSH）、`ssh_to_ws`（读 SSH 发 WebSocket）；
  - 支持前端发来的 `{"type":"resize", "cols", "rows"}`，调用 `channel.resize_pty`。
- **兼容性**：使用 `run_in_executor` 执行 `client.connect()`，避免在 Python 3.8 上使用不存在的 `asyncio.to_thread` 导致报错。
- **注册**：在 `backend/main.py` 中 `include_router(terminal.router)`。

### 5.2 前端

- **页面**：`frontend/src/views/dashboard/Terminal.vue`
  - 使用 @xterm/xterm + FitAddon，连接/断开按钮，状态：`disconnected` / `connecting` / `connected`；
  - WebSocket URL 使用当前 host + `/api/ws/terminal?token=...`，连接成功后发送一次 resize；
  - ResizeObserver 监听容器变化，调用 `fitAddon.fit()` 并发送 resize 消息；
  - 终端主题为**浅色面板风格**（浅灰背景、深色文字），与仪表盘其他页面一致，不采用黑色终端样式。
- **路由**：在 `frontend/src/router/index.js` 中增加 `/terminal`，对应 `Terminal.vue`。
- **导航**：在 `DashboardLayout.vue` 的 `navItems` 中增加「终端控制」入口。
- **代理**：`frontend/vite.config.js` 中 `/api` 代理配置 `ws: true`，以便开发时 WebSocket 正确转发到后端。

### 5.3 依赖

- 前端：`@xterm/xterm`、`@xterm/addon-fit`（见 `frontend/package.json`）。
- 后端：`paramiko`（已有）、FastAPI WebSocket、数据库与 `utils.cipher`（已有）。

---

## 6. 使用说明（用户侧）

- 使用前须在「系统监控」中配置并保存**远程主机**（IP、端口、用户名、密码）；终端连接使用同一配置。
- 登录后进入「终端控制」，点击「连接」即可在网页中操作远程 Shell；点击「断开」或关闭页面即断开连接。
- 若提示「Remote server not configured」或「请先在系统监控中配置远程服务器」，请先在系统监控页完成远程配置。

---

## 7. 故障排查与已知约束

| 现象 | 可能原因 | 处理建议 |
|------|----------|----------|
| `module 'asyncio' has no attribute 'to_thread'` | 后端运行在 Python 3.8 或更早 | 已改为 `run_in_executor`，兼容 3.7+；若仍报错请确认未使用旧版 `terminal.py` |
| WebSocket 连接失败 / 立即断开 | 未登录、token 过期、或代理未转发 WS | 确认已登录、Vite 代理配置 `ws: true`，生产环境 Nginx 需正确代理 `/api` 的 WebSocket |
| SSH Connection failed | 网络不通、远程未开 SSH、账号密码错误、防火墙 | 在本地 PowerShell 用相同账号测试 SSH；检查后端日志中的异常信息 |
| 终端无输出或乱码 | 编码或 PTY 类型 | 当前后端以 UTF-8 解码 SSH 输出；若远程为其他 locale，可考虑在远程设置 `LANG=en_US.UTF-8` 等 |

**当前限制**：

- 仅使用「系统监控」中配置的**单条**远程主机；多主机选择可在后续版本通过 URL 参数或下拉选择扩展。

---

## 8. 文档与相关文件

- 用户操作说明：见 [用户手册](../user/user-manual.md) 中「远程终端」章节。
- 后端路由：`backend/routers/terminal.py`
- 前端页面：`frontend/src/views/dashboard/Terminal.vue`
- 设计文档（本文）：`docs/design/design-terminal.md`
