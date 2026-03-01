# PanelLab

轻量级服务器运维管理面板（课程实践项目），目标为可实际使用的自用软件，复刻宝塔面板核心能力并适度扩展。

---

## 项目定位与目标

- **产品名**：PanelLab
- **主要用户**：本人自用；若完成用户体系，可支持多用户（内网/公网场景）
- **使用场景**：公网访问为主；部署在固定机器（单台服务器），后续可扩展为管理多台服务器
- **质量目标**：在独立开发与课程学习基础上，尽量提高完成度与可用性；UI 美观、功能清晰可用

---

## 需求概要

| 维度 | 说明 |
|------|------|
| **用户与登录** | 优先做出用户管理系统；完成度较高时可实现：手机号短信登录、微信/QQ 等第三方登录 |
| **必须功能** | 登录、仪表盘、系统监控、网站/反向代理、数据库管理（缺一不可） |
| **扩展方向** | 在可能情况下支持管理多台服务器（宝塔多为单机，可作差异化） |
| **访问与权限** | 单用户即可满足当前需求；公网访问；若仅内网/本机则多用户意义不大 |
| **部署** | 固定机器部署；安装方式后续补充（如一键脚本、Docker、手动步骤）；打包与分发方式待学习 |

详细需求与设计见 [docs/requirements.md](docs/requirements.md)。

---

## 开发环境说明：Windows 开发、Linux 目标

- **结论**：在 Windows 上开发、最终软件跑在 Linux 上，**完全可行**，且很常见。
- **建议做法**：
  - 本地用 Windows + WSL2 或虚拟机跑一个 Linux，在 Linux 里测「真实环境」（Nginx、systemd、路径等）。
  - 后端/前端代码在 Windows 用 VS Code/Cursor 写，在 WSL 或远程 Linux 上运行与调试。
  - 涉及系统调用的部分（如执行 shell、读 `/etc`、进程列表）尽量抽象成接口，在开发机用 Mock 或 WSL 测，定期在真实 Linux 上做一次集成测试。
- **注意**：路径、换行符、权限等要在 Linux 上验证，避免只在本机 Windows 通过就认为没问题。

---

## 功能范围

### 必须实现（MVP）

- 用户体系：登录 / 登出（至少账号密码）
- 仪表盘：概览信息（系统状态、快捷入口）
- 系统监控：CPU、内存、磁盘、网络等
- 网站与反向代理：站点列表、添加/编辑、反向代理配置
- 数据库管理：数据库列表、创建/删除、基础管理

### 可选 / 进阶

- 用户管理：多用户、角色（如管理员/只读）
- 登录方式：手机号+短信验证码、微信/QQ 等第三方登录
- 计划任务：定时任务配置与管理
- 安全与加固：防火墙规则、SSH、日志审计等

### 扩展方向

- **多服务器管理**：在单机版稳定后，可考虑「面板中台 + 多台被控机」架构，实现集中管理多台 Linux 服务器。

---

## 开发计划（建议顺序）

在无严格时间约束下，建议按「可运行、可演示」的节奏推进，每阶段都有一块可用的功能。

| 阶段 | 内容 | 产出 |
|------|------|------|
| **0** | 技术选型与 Hello 面板 | 前后端打通、本地可访问空白面板 |
| **1** | 用户体系与仪表盘 | 登录/登出、仪表盘骨架与基础数据展示 |
| **2** | 系统监控与进程管理 | CPU/内存/磁盘/网络监控、进程列表与操作 |
| **3** | 网站与反向代理 | 站点 CRUD、Nginx 反向代理配置与生效 |
| **4** | 数据库与计划任务 | MySQL 库/用户管理、计划任务（cron）管理 |
| **5** | 安全与部署、多机扩展（可选） | 权限与审计、部署文档、可选多服务器架构 |

计划任务若时间紧可并入阶段 4 或延后；多服务器、第三方登录等放在主流程稳定后再做。

---

## 技术栈与可选技术点

### 当前技术栈

- **后端**：Python + FastAPI
- **前端**：Vue 3
- **数据库**：MySQL

### 可选技术点（可按兴趣/课程选做）

- **前端**：Vue 3 组合式 API、Pinia 状态管理、前端路由与权限、UI 组件库（Element Plus / Naive UI / 等）统一风格
- **后端**：FastAPI 依赖注入与中间件、JWT 与 Session、异步与后台任务（Celery/ARQ 等）
- **运维与部署**：Docker 容器化、Nginx 反向代理与 HTTPS、systemd 服务、简单 CI（如 GitHub Actions）
- **第三方**：短信接口（阿里云/腾讯云）、微信/QQ OAuth、对象存储（可选）

---

## 环境要求

- **Python** 3.10+
- **Node.js** 18+（前端开发）
- **MySQL** 8.0+（或 5.7+）

## 目录结构

```
PanelLab/
├── backend/     # 后端 API 服务（FastAPI）
├── frontend/    # 前端 Web 应用（Vue 3）
├── docs/        # 项目文档
└── README.md
```

## 环境配置

### 1. 数据库（MySQL）

1. 安装并启动 MySQL（本地或 Docker 均可）。
2. 创建专用于本项目的数据库，例如：
   ```sql
   CREATE DATABASE panel_lab CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
   CREATE USER 'panel_lab'@'localhost' IDENTIFIED BY '你的密码';
   GRANT ALL PRIVILEGES ON panel_lab.* TO 'panel_lab'@'localhost';
   FLUSH PRIVILEGES;
   ```
3. 在 `backend` 目录下复制环境变量示例并填写实际配置：
   ```bash
   cd backend
   cp .env.example .env
   ```
   编辑 `.env`，填写 `MYSQL_*` 等变量（数据库名、用户名、密码、主机、端口）。

### 2. 后端

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

### 3. 前端

```bash
cd frontend
npm install
```

## 开始开发

- **后端**：在 `backend` 目录激活虚拟环境后运行：
  ```bash
  uvicorn main:app --reload
  ```
  服务地址默认：http://localhost:8000 ；接口测试：http://localhost:8000/api/hello
- **前端**：在 `frontend` 目录运行 `npm run dev`，浏览器打开终端提示的地址（如 http://localhost:5173 ）。页面中的「接口测试（环境检验）」会请求后端，成功则说明前后端已打通。
