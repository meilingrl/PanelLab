# 如何确认 MySQL 是否创建好

按下面步骤检查 MySQL 已安装、已建库、已建用户且能连接。

---

## 1. 确认 MySQL 服务在运行

- **Windows**：任务管理器 → 服务，找 `MySQL` 或 `MySQL80`（或你安装时的服务名），状态应为「正在运行」。或在「服务」应用里查看。
- **WSL / Linux**：终端执行 `sudo systemctl status mysql` 或 `sudo service mysql status`，应显示 active (running)。

---

## 2. 用命令行连上 MySQL

用**管理员账号**（如 `root`）登录：

```bash
mysql -u root -p
```

提示输入密码后，若进入 `mysql>` 提示符，说明 MySQL 已安装且服务正常。

---

## 3. 确认数据库和用户是否存在

在 `mysql>` 里依次执行：

```sql
-- 查看是否有 panel_lab 库
SHOW DATABASES LIKE 'panel_lab';

-- 查看是否有 panel_lab 用户（MySQL 用户存在 user 表里）
SELECT user, host FROM mysql.user WHERE user = 'panel_lab';
```

- 若 `SHOW DATABASES` 结果里有一行 `panel_lab`，说明**数据库已创建**。
- 若 `SELECT` 能查到一行（如 `panel_lab` @ `localhost`），说明**用户已创建**。

---

## 4. 用项目用户测一次登录（可选）

退出 root（输入 `exit`），用项目配置的用户连接，并指定库名：

```bash
mysql -u panel_lab -p panel_lab
```

- 能连上且提示符变为 `mysql>`，说明**用户名、密码、库名**都对，PanelLab 的 `.env` 里 `MYSQL_USER`、`MYSQL_PASSWORD`、`MYSQL_DATABASE` 应和这里一致。
- 若报错 `Access denied`，多半是密码错或该用户没有从本机连接的权限，需回到 root 检查/重建用户和授权（见 README 环境配置）。

---

## 5. 若还没建库、建用户

用 root 登录后执行（把 `你的密码` 换成实际密码）：

```sql
CREATE DATABASE panel_lab CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'panel_lab'@'localhost' IDENTIFIED BY '你的密码';
GRANT ALL PRIVILEGES ON panel_lab.* TO 'panel_lab'@'localhost';
FLUSH PRIVILEGES;
```

然后再用第 4 步测一次 `mysql -u panel_lab -p panel_lab`。

---

**总结**：能执行 `mysql -u panel_lab -p panel_lab` 并进入 `mysql>`，就说明 MySQL 已为 PanelLab 创建好，后端可正常连库。
