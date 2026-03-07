# 远程 Linux 监控：需求与设计（Plan）

本文档为「远程 Linux 监控」的 Plan，与 [design-monitor.md](design-monitor.md) 本机监控共用同一套数据结构和前端展示；格式与 [docs/README.md](README.md) 中「Plan 格式说明」一致。

---

## 1. 目标与范围

| 项目 | 说明 |
|------|------|
| 目标 | 在系统监控页除「本机」外，支持选择「远程 Linux 服务器」，展示该远程主机的 CPU、内存、磁盘、网络等指标。 |
| 方式 | 通过 **SSH** 连接远程主机，在目标机上执行一段**采集脚本**（输出与本机接口同结构的 JSON），后端解析后返回。不在远程部署常驻 Agent。 |
| 本阶段 | 支持**单台**远程主机，连接信息在**网页上填写**并存入数据库（加密存储密码）。 |

---

## 2. 功能需求

在系统监控页支持选择「本机」或「远程服务器」；远程需在页内配置 SSH 连接信息并加密存储；展示逻辑与本机一致（同一套卡片与进度条）。前置条件见下。

---

## 3. 前置条件（远程主机）

- 可 **SSH 登录**（本实现使用**密码**认证；密钥可后续扩展）。
- 已安装 **Python 3** 且已安装 **psutil**（`pip install psutil`），用于在远程执行采集脚本并输出 JSON。  
  若未安装，可在远程执行：`pip3 install --user psutil` 或由管理员安装。

---

## 4. 数据与存储

- 表 `monitor_remote_config`：单条记录（host, port, username, password_encrypted）；密码由应用 `secret_key` 派生密钥、Fernet 加密后写入。详见「6. 后端实现要点」。

---

## 5. 接口约定（原「配置方式」并入前端要点）

### 配置方式（网页）

- 在仪表盘 **系统监控** 页底部有「远程服务器连接」区块。
- 用户填写 **主机（IP 或域名）**、**端口**（默认 22）、**用户名**、**密码** 后点击「保存并连接」。
- 配置写入数据库表 `monitor_remote_config`，密码使用应用 `secret_key` 派生密钥做 Fernet 加密后存储。
- 保存成功后，上方「监控目标」下拉框会出现「远程服务器」，选择即可查看该主机监控数据。

### 远程配置接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/monitor/remote-config | 需登录；返回当前是否已配置及 host/port/username（不含密码）。 |
| PUT | /api/monitor/remote-config | 需登录；body `{ host, port, username, password }`，保存或更新一条配置，密码加密存储。 |

### 获取监控目标列表

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/monitor/hosts | 需登录；返回可选的监控目标列表。 |

**响应示例：**

```json
{
  "hosts": [
    { "id": "local", "label": "本机" },
    { "id": "remote", "label": "远程服务器" }
  ]
}
```

- 未在网页保存过远程配置时，`hosts` 仅包含 `{ "id": "local", "label": "本机" }`。

### 获取指定目标的监控快照

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/monitor/stats?target=local | 本机快照（默认）。 |
| GET | /api/monitor/stats?target=remote | 远程主机快照（通过 SSH 执行采集脚本后返回）。 |

- **target** 缺省为 `local`。  
- 当 `target=remote` 且未配置远程或 SSH 失败时，返回 503 或 200 + 错误信息（如 `{"detail": "远程连接失败"}`），前端展示友好提示。

---

## 6. 后端实现要点

- **存储**：表 `monitor_remote_config` 存单条记录（host, port, username, password_encrypted）；密码用 `utils.cipher` 中 Fernet（密钥由应用 `secret_key` 派生）加密后写入。
- **依赖**：使用 **paramiko** 建立 SSH 连接，在远程执行采集脚本；当前仅支持密码认证。
- **采集脚本**：一段在远程通过 `python3 -`（从 stdin 读入）执行的 Python 代码，内部使用 psutil 采集 CPU、内存、磁盘、网络，并 `print(json.dumps({...}))` 输出与当前本机接口**相同结构**的 JSON；后端解析该输出后返回。
- **超时**：SSH 连接与命令执行设置合理超时（如 15 秒），避免长时间阻塞。
- **安全**：密码仅加密存储在库中，接口不返回密码；日志中不记录密码。

---

## 7. 前端实现要点

- **目标选择**：在系统监控页顶部「监控目标」下拉框，选项来自 `GET /api/monitor/hosts`（本机 + 远程，若已配置）。
- **远程配置**：页面底部「远程服务器连接」区块：若未配置或点击「修改配置」，展示表单（主机、端口、用户名、密码），提交调用 `PUT /api/monitor/remote-config`，成功后刷新 hosts 并可选展示「已配置：host:port」。
- **请求**：切换目标后请求 `GET /api/monitor/stats?target=local` 或 `?target=remote`，展示逻辑与本机一致（同一套卡片与进度条）。
- **错误**：远程不可用或超时时，展示「远程服务器暂不可用」等提示；未配置时选择远程则提示「请先在下方配置远程服务器连接」。

---

## 7. 与后续阶段衔接

- 依赖本机监控 [design-monitor.md](design-monitor.md) 的数据结构与前端展示；后续可扩展多台远程、密钥认证、采集脚本可配置等。

---

## 8. 本阶段完成清单

- [x] 需求与设计明确
- [x] 表与加密、GET/PUT remote-config、hosts、stats?target=remote
- [x] 前端目标选择与远程配置区块
- [ ] Linux 上 SSH 与远程采集联调验证

---

## 9. 文档更新记录

| 日期 | 变更说明 |
|------|----------|
| 2026-03 | 初版：远程 Linux 监控方式、接口与前后端要点 |
| 2026-03 | 统一为 Plan 格式（章节编号与命名）；4 数据与存储、8 衔接、9 完成清单 |
