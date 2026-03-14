# 开发环境日常启动

配置完成后，日常开发只需启动后端和前端。

---

## 前置

- **MySQL**：已安装为 Windows 服务并配置为开机自启（见下方「配置 MySQL 自启动」），一般无需手动操作。若需手动启动：管理员 CMD 执行 `net start MySQL96`（服务名以本机为准）。

---

## 配置 MySQL 自启动

MySQL 已安装为 Windows 服务但未开机自启时，可设为自动启动（需**管理员权限**）：

**方式一：命令行**

以**管理员**身份打开 CMD 或 PowerShell，执行（将 `MySQL96` 改为本机实际服务名）：

- **PowerShell** 中 `sc` 是 Set-Content 的别名，必须用 **`sc.exe`** 调用系统命令：
  ```powershell
  sc.exe config MySQL96 start= auto
  ```
- **CMD** 中可直接写：
  ```cmd
  sc config MySQL96 start= auto
  ```

然后可立即启动服务（可选）：
  ```powershell
  net start MySQL96
  ```

**方式二：图形界面**

1. `Win + R` → 输入 `services.msc` → 回车
2. 在列表中找到 MySQL 服务（如「MySQL96」）
3. 双击 →「启动类型」选「自动」→ 确定
4. 若当前未运行，可点击「启动」

查看本机 MySQL 服务名：管理员 CMD 执行 `sc.exe query type= service state= all | findstr /i mysql`（PowerShell 中必须用 `sc.exe`，否则 `sc` 会解析为 Set-Content）。

---

## MySQL 服务无法启动时

### 情况一：报错「拒绝访问」或「无法启动」

**原因**：启动/停止 Windows 服务需要**管理员权限**。

**解决**：以**管理员**打开 CMD 或 PowerShell，执行 `net start MySQL96`（或 `Start-Service MySQL96`）；或在 `services.msc` 中右键 MySQL96 →「启动」。

---

### 情况二：管理员启动后提示「服务启动后停止」「服务没有报告任何错误」

**原因**：MySQL 以 Windows 服务方式启动时，若未指定配置文件，工作目录与数据目录可能与命令行运行不一致，导致进程启动后立即退出。

**解决**（Scoop 安装的 MySQL，路径以本机为准）：

1. **确认 `my.ini` 已包含 `basedir`**（便于服务从任意工作目录找到安装路径）  
   打开 `E:\Scoop\apps\mysql\current\my.ini`，在 `[mysqld]` 下应有：
   ```ini
   basedir=E:/Scoop/apps/mysql/current
   datadir=E:/Scoop/persist/mysql/data
   ```
   若没有 `basedir`，补上即可。

2. **让服务使用该配置文件**  
   以**管理员**打开 CMD（PowerShell 中请用 `sc.exe`），执行（路径按本机修改）：
   ```cmd
   sc config MySQL96 binPath= "E:\Scoop\apps\mysql\current\bin\mysqld.exe --defaults-file=E:\Scoop\apps\mysql\current\my.ini MySQL96"
   ```
   然后再次启动服务：
   ```cmd
   net start MySQL96
   ```

3. **若仍失败**  
   查看 MySQL 错误日志（`E:\Scoop\persist\mysql\data\*.err` 或 `E:\Scoop\apps\mysql\current\data\*.err`）最后几行，排查端口占用或数据目录权限。

---

### 临时方案：不用服务，命令行前台跑 MySQL（无需管理员）

在**单独**终端执行（Scoop 默认路径示例）：
```powershell
E:\Scoop\apps\mysql\current\bin\mysqld.exe --defaults-file=E:\Scoop\apps\mysql\current\my.ini --standalone --console
```
看到 `ready for connections` 即表示就绪；关闭窗口会停止 MySQL。

---

## 启动后端

在项目目录下打开终端（PowerShell 或 CMD）：

```bash
cd backend
.\.venv\Scripts\activate
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

- 默认地址：<http://localhost:8000>
- 健康检查：<http://localhost:8000/api/health>

---

## 启动前端

**另开一个终端**：

```bash
cd frontend
npm run dev
```

- 默认地址：<http://localhost:5173>
- 首次使用需先执行 `npm install`（仅一次）

---

## 默认登录

- 用户名：`admin`
- 密码：`backend/.env` 中 `INIT_ADMIN_PASSWORD`（默认 `admin`）
- 修改密码：在 `backend` 目录、已激活 venv 下执行 `python change_password.py admin 新密码`
