# PanelLab 完整测试步骤

按顺序执行以下步骤，完成「登录功能」从环境到浏览器、再到（可选）WSL 的完整验证。

---

## 一、前置条件

1. **MySQL** 已安装并运行，且已创建数据库与用户：
   - 数据库：`panel_lab`
   - 能用于连接的账号（如 `panel_lab` 或 `root`）及密码  
   验证方式见 [docs/check-mysql.md](check-mysql.md)。

2. **后端 `.env`** 已配置：  
   在 `backend` 目录存在 `.env`，且已填写 `MYSQL_HOST`、`MYSQL_USER`、`MYSQL_PASSWORD`、`MYSQL_DATABASE`、`SECRET_KEY`、`INIT_ADMIN_PASSWORD`（可选，默认 `admin`）。

3. **Python 3.10+**、**Node.js 18+** 已安装。

---

## 二、后端：安装依赖与初始化

在项目根目录下打开终端，执行：

```bash
cd backend
```

1. **创建并激活虚拟环境**（若尚未创建）：
   - Windows (CMD/PowerShell)：  
     `python -m venv .venv` → `.venv\Scripts\activate`
   - WSL / Linux：  
     `python3 -m venv .venv` → `source .venv/bin/activate`  
   （WSL 下若项目在 `/mnt/e/...`，建议虚拟环境建在 `~/panellab-venv`，见 [linux-testing-wsl-vm.md](linux-testing-wsl-vm.md)）

2. **安装依赖**：  
   `pip install -r requirements.txt`  
   - 若出现 SSL 或连不上 PyPI，改用国内镜像并信任该域名：  
     `pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn`  
   - **若依旧连不上**：先试关闭 VPN/代理，或换手机热点；再试临时跳过 SSL 校验（仅限本机开发）：  
     `pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn --trusted-host pypi.org --trusted-host files.pythonhosted.org`  
     若仍失败，可在能联网的电脑/网络上下载好各包（或使用 `pip download -r requirements.txt -d ./wheels` 导出 wheel），拷贝到本机后执行：  
     `pip install --no-index --find-links=./wheels -r requirements.txt`

3. **初始化数据库**（仅首次需要，创建用户表并写入默认管理员）：  
   `python -m init_db`  
   成功后会提示已创建管理员，用户名 `admin`，密码为 `.env` 中的 `INIT_ADMIN_PASSWORD`（默认 `admin`）。  
   之后若需修改 admin 密码：`python change_password.py admin 你的新密码`。

4. **启动后端**：  
   `python -m uvicorn main:app --reload --host 0.0.0.0`  
   （若直接输 `uvicorn` 报「不是内部或外部命令」，请用 `python -m uvicorn`。）  
   保持该终端不关闭，默认地址：http://localhost:8000 。

---

## 三、前端：安装依赖与启动

**新开一个终端**，在项目根目录执行：

```bash
cd frontend
npm install
npm run dev
```

按提示在浏览器打开地址（如 http://localhost:5173 ），保持该终端不关闭。

---

## 四、浏览器功能测试

1. **打开登录页**  
   访问前端地址（如 http://localhost:5173 ），应自动进入登录页（居中白框、左右分栏、线条背景）。

2. **登录**  
   - 用户名：`admin`  
   - 密码：`admin`（或你在 `.env` 中设置的 `INIT_ADMIN_PASSWORD`）  
   点击「登录」，应跳转到仪表盘，顶部显示 PanelLab、主题切换、退出按钮。

3. **登出**  
   点击「退出」，应回到登录页；再次访问 `/` 应被重定向到登录页。

4. **错误密码**  
   在登录页输入错误密码，应出现「用户名或密码错误」等提示，且不跳转。

5. **主题切换**（可选）  
   在登录页或仪表盘点击 🌙/☀️，界面应在明暗主题间切换，刷新后主题保持。

---

## 五、接口快速验证（可选）

后端已启动时，可用浏览器或 curl 做接口检查：

- **健康检查**：  
  打开 http://localhost:8000/api/health ，应返回 `{"status":"ok"}`。

- **登录接口**：  
  ```bash
  curl -X POST http://localhost:8000/api/auth/login -H "Content-Type: application/json" -d "{\"username\":\"admin\",\"password\":\"admin\"}"
  ```  
  应返回包含 `token` 和 `user` 的 JSON；若密码错误则返回 401。

---

## 六、WSL / Linux 环境测试（可选）

若需在 WSL 或虚拟机 Linux 上验证同一套代码：

1. 在 WSL 或 Linux 中进入项目目录（Windows 盘在 WSL 下为 `/mnt/e/Dev/Projects/PanelLab` 等）。
2. 按 **二、三** 在 WSL/Linux 中执行：  
   `cd backend` → 激活 venv → `pip install -r requirements.txt` →（若库未初始化）`python -m init_db` → `uvicorn main:app --reload --host 0.0.0.0`。  
   前端可在 Windows 继续用 `npm run dev`，浏览器访问同一前端地址即可（后端在 WSL 时仍为 localhost:8000）。
3. 重复 **四** 的登录、登出、错误密码、主题切换，确认行为一致。

详细 WSL 配置见 [docs/linux-testing-wsl-vm.md](linux-testing-wsl-vm.md)。

---

## 七、测试通过标准

- 后端 `uvicorn` 与前端 `npm run dev` 均无报错。
- MySQL 连接正常，`python -m init_db` 仅首次需执行且成功。
- 浏览器能打开登录页，使用 admin/（你设的密码）能登录并进入仪表盘。
- 退出后回到登录页，未登录访问 `/` 会重定向到登录页。
- 错误密码有明确提示。
- （可选）在 WSL 或 Linux 中按上述步骤跑通相同流程。

以上全部通过即表示当前「登录功能」完整测试通过。
