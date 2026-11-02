# PanelLab

面向个人与小团队自托管运维的管理面板。本分支聚焦**可审查、可恢复的本机 Nginx 站点变更**：预览实际配置差异、拒绝过期与冲突操作、记录应用和恢复结果。

已有 SSH 终端、监控、服务器库和 MySQL 管理。远程终端/监控不等于远程站点部署；站点和数据库管理作用于面板配置的共享服务。生产就绪边界及实际验证见 [改进与验证记录](docs/project/production-readiness-20261102.md)。

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
| **目标** | 做深站点变更的可解释性与失败恢复；真实应用目标 Linux |

---

## 功能范围

### 已实现 / MVP

- **用户与登录**：账号密码、修改密码、JWT；生产关闭注册及演示登录，共享资源限制到运维账号
- **仪表盘**：概览、系统状态、快捷入口、多页面骨架
- **系统监控**：本机与远程 Linux 的 CPU、内存、磁盘、网络监控
- **网页端终端**：基于 WebSocket 的 SSH 终端，连接远程服务器执行命令
- **网站与反向代理**：静态站点/反向代理、配置差异预览、五分钟确认凭证、冲突检测、失败恢复、变更记录
- **数据库管理**：MySQL 库与用户管理；计划任务仍未实现

### 历史构想 / 后续候选

以下是历史功能构想，不是本轮承诺。当前优先级与不做项见 [站点变更设计](docs/design/design-safe-site-changes.md)。

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
