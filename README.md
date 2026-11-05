# PanelLab

面向个人与小团队的**远程任务工作台**：把服务器上的训练实验、网站和定时任务关联到具体进程、日志与文件，人通过界面查看，外部Agent通过相同API、CLI或可选MCP工具读取任务现场。当前为本地运行的Web应用及CLI，目标远程系统为Linux。

工作台使用统一服务器记录和显式主机公钥信任，提供任务登记、现场观察、目录点选、日志尾部、文件下载和上报记录。具体使用见 [工作台指南](docs/user/remote-workspace.md)，实际验证与限制见 [工作台基础记录](docs/project/remote-workspace-20261103.md)、[实时观测记录](docs/project/live-observation-20261103.md) 与 [MCP接入记录](docs/project/mcp-access-20261105.md)。

同时保留可审查、可恢复的本机Nginx站点变更，以及原有监控、终端和MySQL管理。远程任务工作台不等于远程站点部署；站点和数据库管理仍作用于面板配置的共享服务。上一轮证据见 [站点变更记录](docs/project/production-readiness-20261102.md)。

---

## 快速开始

```bash
# 1. 创建 MySQL 数据库并配置 backend/.env（见下方「环境配置」）
# 2. 后端
cd backend && python -m venv .venv && .venv\Scripts\activate  # Windows
pip install -r requirements.txt && python -m init_db && uvicorn main:app --reload

# 3. 前端（新开终端）
cd frontend && npm install && npm run dev
```

浏览器访问前端提示的地址（如 http://localhost:5173），默认账号 `admin`，密码见 `.env` 中 `INIT_ADMIN_PASSWORD`（默认 `admin`）。

---

## 项目概览

| 项目 | 说明 |
|------|------|
| **定位** | 面向可信运维人员的自托管工具；不提供隔离的多租户托管 |
| **场景** | 公网/内网访问；单机部署，后续可扩展多台服务器集中管理 |
| **目标** | 不记命令和路径也能回到任务现场；人和Agent共用任务ID、接口和权限 |

---

## 功能范围

### 已实现 / MVP

- **远程任务工作台**：点选进程/目录/日志，固定已有任务，查看资源与进程身份，浏览与下载文件，记录显式上报的执行结果
- **可控实时观测**：概览按2–60秒间隔跟随，展示CPU和各网卡区间速率；暂停、断线和任务改绑时明确区分旧数据
- **统一机器接口**：`python scripts/panellab.py --help`；CLI通过同一HTTP API访问数据，不另走SSH
- **MCP接入**：本地stdio提供8项可发现的只读工具，支持配置生成与API连接检查；按 [接入指南](docs/user/mcp-access.md) 单独安装，不含内置聊天Agent
- **可信连接**：主机指纹确认、公钥变化拒绝、短期单次终端票据；可选Tailscale设备发现和认证模式（实际tailnet未验收）

- **用户与登录**：账号密码、修改密码、JWT；生产关闭注册及演示登录，共享资源限制到运维账号
- **仪表盘**：概览、系统状态、快捷入口、多页面骨架
- **系统监控**：本机与远程 Linux 的 CPU、内存、磁盘、网络监控
- **网页端终端**：基于 WebSocket 的 SSH 终端，连接远程服务器执行命令
- **网站与反向代理**：静态站点/反向代理、配置差异预览、五分钟确认凭证、冲突检测、失败恢复、变更记录
- **数据库管理**：MySQL 库与用户管理；计划任务仍未实现

### 历史构想 / 后续候选

以下是历史功能构想，不是本轮承诺。当前优先级见 [路线图](docs/project/feature-roadmap.md)，工作台边界见 [设计](docs/design/design-remote-workspace.md)。

- 登录增强：手机验证码、微信/QQ 扫码
- 终端增强：常用指令库、简易 AI 对话
- 用户体验：服务指南、厂商导航、反馈信箱、服务器账号密码库
- 多服务器：地图展示多机分布与状态
- 网站与域名：域名绑定、SSL 证书
- 服务器文件管理：远程文件浏览、上传下载、在线编辑
- 流量与访问统计：流量监控、访问日志解析与统计

详见：[功能构想与路线图](docs/project/feature-roadmap.md)

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python 3.10+、FastAPI、MySQL 8.0+ |
| 前端 | Vue 3、Vite、Pinia |
| 终端 | xterm.js、WebSocket、Paramiko（SSH） |

---

## 目录结构

```
PanelLab/
├── backend/     # FastAPI 后端、API、WebSocket、SSH 桥接
├── frontend/    # Vue 3 前端
├── docs/        # 需求、设计（Plan）、环境与部署文档
└── README.md
```

---

## 环境要求

- **Python** 3.10+
- **Node.js** 20.19+ 或 22.12+（满足 Vite 7 要求）
- **MySQL** 8.0+（本轮真实验证版本为 8.4.9）

---

## 环境配置

### 1. 数据库

安装并启动 MySQL，创建库与用户：

```sql
CREATE DATABASE panel_lab CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'panel_lab'@'localhost' IDENTIFIED BY '你的密码';
GRANT ALL PRIVILEGES ON panel_lab.* TO 'panel_lab'@'localhost';
FLUSH PRIVILEGES;
```

### 2. 后端

```bash
cd backend
cp .env.example .env
# 编辑 .env：MYSQL_*、INIT_ADMIN_PASSWORD 等

python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m init_db    # 首次运行：建表并创建默认管理员
```

### 3. 前端

```bash
cd frontend
npm install
```

---

## 运行与登录

| 步骤 | 命令 | 说明 |
|------|------|------|
| 启动后端 | `uvicorn main:app --reload`（在 backend 目录、已激活 venv） | 默认 http://localhost:8000 |
| 启动前端 | `npm run dev`（在 frontend 目录） | 默认 http://localhost:5173 |
| 首次登录 | 用户名 `admin`，密码为 `.env` 中 `INIT_ADMIN_PASSWORD` | 修改密码：`python change_password.py admin 新密码` |

- 健康检查：http://localhost:8000/api/health  
- 完整测试与验收步骤：[docs/environment/testing-steps.md](docs/environment/testing-steps.md)

---

## 开发说明

- **开发环境**：推荐在 Windows 上开发，用 WSL2 或虚拟机中的 Linux 做真实环境测试（Nginx、路径、权限等）。
- **目标环境**：软件面向 Linux 部署；涉及系统调用的部分需在 Linux 上验证。  
  详见：[docs/environment/linux-testing-wsl-vm.md](docs/environment/linux-testing-wsl-vm.md)

---

## 工作台范围

- 优先接入已有任务。登记/移除只改面板元数据，不启动或停止训练，不安装cron，不删除远程文件。
- 进程存在不代表训练推进；上报记录不代表独立验证成功。网站URL是打开入口，未自动做健康检查。
- 文本/日志读取上限64KiB，下载上限16MiB；符号链接与特殊文件不跟随。
- CPU占比与各网卡近似速率需两次有效区间采样，自动跟随默认关闭；服务器共享资源不能归因于某个任务，累计字节与速率分开显示。缺少GPU采集工具时显示不可用。
- Tailscale可选。软件不改变tailnet授权，没有内置LLM聊天网关；可选MCP为本地stdio只读适配器，仍使用原账号Bearer权限。

## 站点变更与生产模式

- 所有创建、编辑、重新应用、删除都先预览再确认；预览不会执行 Nginx。
- 确认前若表单、数据库或配置文件发生变化，需重新预览。失败时查看变更记录，不要盲目重复提交。
- `NGINX_SKIP_APPLY=true` 仅用于开发，显示“模拟”；不会标为真实已应用。
- `APP_ENV=production` 要求非示例、至少 32 字符的 `SECRET_KEY` 和至少 12 字符的 `INIT_ADMIN_PASSWORD`，禁止演示登录及跳过应用。`OPERATOR_USERNAMES=admin` 指定可信运维账号。现有账号密码不会因修改初始密码配置而自动轮换。
- 旧 `/api/sites` 写接口返回 428；客户端应迁移到 `/api/site-changes/preview` 与 `/execute`。GET 查询保留。
- `nginx -t` 和 reload 命令成功不等于业务健康检查通过。完整流程见 [使用说明](docs/user/user-manual.md)。

## 文档索引

| 文档 | 说明 |
|------|------|
| [docs/README.md](docs/README.md) | 文档总索引与 Plan 列表 |
| [docs/project/requirements.md](docs/project/requirements.md) | 总需求与阶段规划 |
| [docs/project/feature-roadmap.md](docs/project/feature-roadmap.md) | 功能构想与实现顺序 |
| [docs/user/user-manual.md](docs/user/user-manual.md) | 用户使用说明书 |
| [docs/environment/testing-steps.md](docs/environment/testing-steps.md) | 完整测试步骤 |
| [docs/deploy/deploy.md](docs/deploy/deploy.md) | 部署说明 |
