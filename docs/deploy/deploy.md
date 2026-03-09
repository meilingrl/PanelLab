# PanelLab 生产部署说明

本文档说明如何用 Docker 或传统方式部署 PanelLab。

---

## 一、Docker 部署（推荐）

### 1.1 使用 docker-compose（含 MySQL）

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

### 1.2 仅使用 Dockerfile（自备 MySQL）

若已有 MySQL 服务：

```bash
docker build -t panellab .
docker run -d -p 8000:8000 \
  -e MYSQL_HOST=你的MySQL主机 \
  -e MYSQL_USER=panel_lab \
  -e MYSQL_PASSWORD=你的密码 \
  -e MYSQL_DATABASE=panel_lab \
  -e SECRET_KEY=强随机密钥 \
  -e INIT_ADMIN_PASSWORD=管理员密码 \
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

## 三、安全与生产建议

- **SECRET_KEY**：生产环境必须改为强随机字符串。  
- **INIT_ADMIN_PASSWORD**：首次初始化后建议用「修改密码」或 `change_password.py` 修改。  
- **HTTPS**：生产建议在 Nginx 或负载均衡层配置 TLS，后端可仅监听内网。  
- **防火墙**：仅开放必要端口（如 8000 或 80/443）。

---

## 四、文档更新记录

| 日期       | 说明           |
|------------|----------------|
| 2026-03-07 | 初版：Docker 与传统部署步骤 |
