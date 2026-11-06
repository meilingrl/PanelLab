# PanelLab 部署说明与生产边界

本文档区分本地演示与生产模式。当前分支已补充入口防护与站点变更流程，但不代表整站生产验收；先读[验证记录与剩余风险](../project/production-readiness-20261102.md)。

**现有 docker-compose.yml 是本地演示配置**，含固定数据库口令，不可直接公开部署。应用 Dockerfile 也未安装 Nginx；站点管理作用于应用所在文件系统和命令环境，不会自动管理宿主机 Nginx。不要为方便而给面板挂载生产 Docker socket。真实站点应用优先在明确权限的 Linux 环境安装 Nginx 并配置下述路径/命令。

---

## 一、Docker 本地演示

### 1.1 启动本机面板（macOS / Linux）

前置条件为 Python 3、本机 Docker 引擎，以及支持 `up --wait` 的 Docker Compose。在项目根目录执行：

```bash
python3 scripts/local_panel.py           # 等待 MySQL 就绪，建表并启动打包的前后端
python3 scripts/local_panel.py password  # 仅在自己的终端查看初始管理员密码
```

打开 [本机面板](http://127.0.0.1:8765)，账号为 `admin`。首次构建会联网下载镜像、npm 和 Python 依赖。应用容器启用生产模式的登录限制，Web 仅绑定本机回环地址，MySQL 只在该 Compose 项目的网络中提供服务。

- **暂停与继续**。执行 `python3 scripts/local_panel.py stop`，之后运行原启动命令。每次启动会复用镜像构建缓存，已有用户跳过初始化。修改过登录密码后，请用新密码登录。
- **自选端口**。例如 `python3 scripts/local_panel.py --port 8766`。每次启动传入同一端口即可使用同一浏览器地址。
- **凭据与数据**。首次运行在 `data/local-panel/panellab-local.json` 中生成独立的项目名、初始管理员密码、数据库口令以及签名和加密使用的 `SECRET_KEY`。此文件仅允许当前用户读写。MySQL 数据在该项目的 Docker 命名卷中。备份或迁移时请同时保存该文件和数据库。容器管理员能查看容器配置。
- **隔离第二套面板**。在启动命令中同时指定 `--state-dir /另一个私有目录 --port 8767`。查看初始密码、日志、停止或重新启动时沿用相同的 `--state-dir`。
- **排查失败**。构建失败时检查 Docker 引擎、镜像仓库及依赖源的网络连接，修复后重试启动命令。容器启动失败可运行 `python3 scripts/local_panel.py logs` 查看最近日志。MySQL 报 `No space left on device` 时检查 Docker 引擎的数据盘空间。端口占用时改用空闲端口。

### 1.2 旧版演示配置（固定口令）

1. **克隆并进入项目目录**
   ```bash
   cd /path/to/PanelLab
   ```

2. **启动 MySQL 并等待就绪**
   ```bash
   docker compose up -d mysql
   # 等待约 30 秒，或 docker compose logs mysql 看到 "ready for connections"
   ```

3. **构建并启动应用**
   ```bash
   docker compose up -d --build app
   ```

4. **初始化数据库与管理员（仅首次）**
   ```bash
   docker compose exec app python -m init_db
   ```
   默认管理员：用户名 `admin`，密码 `admin`（由 `INIT_ADMIN_PASSWORD` 决定）。

5. **访问**
   - 浏览器打开：http://localhost:8000  
   - 前端已打包进镜像，由后端托管根路径，无需单独起前端。

### 1.3 仅使用 Dockerfile（自备 MySQL）

若已有 MySQL 服务：

```bash
docker build -t panellab .
docker run -d -p 8000:8000 \
  -e MYSQL_HOST=你的MySQL主机 \
  -e MYSQL_USER=panel_lab \
  -e MYSQL_PASSWORD=你的密码 \
  -e MYSQL_DATABASE=panel_lab \
  -e APP_ENV=production \
  -e SECRET_KEY=替换为至少32字符的独立随机密钥 \
  -e INIT_ADMIN_PASSWORD=替换为至少12字符的独立强密码 \
  panellab
```

首次运行后进入容器执行初始化：

```bash
docker exec -it <容器ID> python -m init_db
```

---

## 二、传统部署（无 Docker）

1. **准备 MySQL**  
   创建数据库与用户，参见 [environment/check-mysql.md](../environment/check-mysql.md)。

2. **后端**  
   - 在 `backend` 目录配置 `.env`（MYSQL_*、SECRET_KEY、INIT_ADMIN_PASSWORD）。  
   - `pip install -r requirements.txt`，执行 `python -m init_db`，再 `uvicorn main:app --host 0.0.0.0 --port 8000`。

3. **前端**  
   - 在 `frontend` 目录执行 `npm ci && npm run build`。  
   - 将 `frontend/dist` 内容拷贝到 `backend/static`，则后端会托管 SPA（见 main.py 中 static 挂载）。  
   - 或使用 Nginx 等反向代理，将 `/api` 转发到后端，其余指向 `dist`。

4. **验收**  
   运行 `python scripts/smoke.py http://你的后端地址`，确认健康检查、登录、监控接口正常。

---

## 三、生产模式配置

先在隔离环境验证，配置示意如下。示意文本不能直接作为生产密码使用：

```dotenv
APP_ENV=production
SECRET_KEY=<至少32字符的独立随机密钥>
INIT_ADMIN_PASSWORD=<至少12字符的强密码>
OPERATOR_USERNAMES=admin
ENABLE_DEMO_AUTH=false
NGINX_SKIP_APPLY=false
NGINX_CONF_DIR=/etc/nginx/panellab.d
NGINX_TEST_CMD=nginx -t
NGINX_RELOAD_CMD=nginx -s reload
NGINX_CMD_TIMEOUT_SECONDS=15
```

- 在 Nginx 主配置的 `http` 段 include 该专用目录的 `*.conf`；面板进程需要此目录读写与指定实例校验/重载的权限。不要让不同实例误用默认 pid 或配置路径；必要时在命令里显式指定 `-c` 和 `-p`。
- 目录专用于 PanelLab，可减少对手写站点的干扰。现有配置可作为预览基线，但不支持符号链接及超限配置；预览不会创建目录或运行 Nginx。
- 生产关闭注册、SMS、微信和 QQ 登录；普通账号不能读取或操作站点与 MySQL 管理。`OPERATOR_USERNAMES` 是可信账号白名单，不提供完整 RBAC 或租户隔离。
- 首次进入生产使用新 SECRET_KEY，使旧开发 JWT 无效；检查已有账号并更新密码。`INIT_ADMIN_PASSWORD` 只影响首次创建，不轮换已有账号。
- 生产校验会拒绝示例/短密钥、短初始密码、演示认证、跳过应用。长度校验不等于密码强度评估。
- 使用 HTTPS 与内网后端监听。SSH 主机密钥信任及终端票据仍需改造，现有功能不得据此宣称已完成全面安全加固。
- 保留数据库、Nginx 主配置和受管目录备份。变更记录不是备份系统；`pending` 或 `recovery_failed` 必须人工核对，禁止无条件重放。
- 原 `/api/sites` 写接口返回 428，客户端应先请求 `/api/site-changes/preview`，再携返回凭证请求 `/execute`。

---

## 四、文档更新记录

| 日期       | 说明           |
|------------|----------------|
| 2026-03-07 | 初版：Docker 与传统部署步骤 |
