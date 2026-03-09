# 使用 WSL 与虚拟机做 Linux 端测试

本文说明如何在 Windows 上通过 **WSL2** 和 **虚拟机（VM）** 完成 PanelLab 的「真实 Linux 环境」开发与测试，与 README 中「开发环境说明」对应。

---

## 一、整体思路

| 环境 | 用途 | 特点 |
|------|------|------|
| **WSL2** | 日常开发、运行与调试、大部分功能测试 | 与 Windows 共享文件、启动快、适合频繁改代码 |
| **虚拟机 Linux** | 定期集成测试、验证 Nginx/systemd/路径/权限 | 更接近生产环境，可装完整 Nginx、MySQL、systemd |

**建议流程**：  
在 Windows 用 Cursor/VS Code 写代码 → 在 **WSL2** 里跑后端/前端并调试 → 涉及系统调用的部分用接口 + Mock 在 WSL 测 → **定期**（如每周或发版前）在虚拟机 Linux 上做一次完整集成测试。

---

## 二、WSL2 配置与使用

### 2.1 安装 WSL2

1. **启用 WSL 与虚拟机平台**（管理员 PowerShell）：
   ```powershell
   wsl --install
   ```
   若已安装过 WSL，可只装新发行版：`wsl --install -d Ubuntu`。

2. 安装完成后重启，按提示设置 Ubuntu 用户名和密码。

3. 确认版本为 WSL2：
   ```powershell
   wsl -l -v
   ```
   若为 WSL1，可转换：`wsl --set-version Ubuntu 2`。

### 2.2 在 WSL 里访问项目代码

- **推荐**：项目放在 **Windows 盘**（如 `E:\Dev\Projects\PanelLab`），在 WSL 里通过 `/mnt/e/Dev/Projects/PanelLab` 访问。
- 这样在 Cursor 里编辑的是同一份文件，WSL 中直接运行，无需拷贝。

在 WSL 终端中：
```bash
cd /mnt/e/Dev/Projects/PanelLab
# 或你的实际盘符路径，如 /mnt/d/...
```

### 2.3 在 WSL 里运行后端

```bash
cd /mnt/e/Dev/Projects/PanelLab/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# 若需 MySQL，在 WSL 里安装或连接 Windows 上的 MySQL（注意 bind-address）
uvicorn main:app --reload --host 0.0.0.0
```

- `--host 0.0.0.0` 便于从 Windows 浏览器访问 `http://localhost:8000`。
- 若前端在 Windows 跑（`npm run dev`），同样可访问该地址做联调。

### 2.4 用 Cursor / VS Code 连到 WSL 运行与调试

1. 安装扩展：**WSL**（微软官方）。
2. 在 Cursor 中：`Ctrl+Shift+P` → 输入 **“WSL: Connect to WSL”**，选择你的 Ubuntu。
3. 在 WSL 窗口中打开项目目录：`/mnt/e/Dev/Projects/PanelLab`。
4. 在 WSL 里打开终端（菜单 **终端 → 新建终端**），即可在 Linux 环境下执行 `uvicorn`、`pip`、`npm` 等。
5. 调试：在 WSL 中运行 `uvicorn` 时，可用 **“Python: Debug”** 或配置 `launch.json` 对 `main:app` 打断点调试。

这样「写代码在 Windows、跑在 WSL」的流程就打通了。

---

## 三、虚拟机 Linux 的用途与搭建

### 3.1 为什么还要用虚拟机？

- **WSL2** 可以跑 Nginx、systemd，但和真实独立 Linux 仍有差异（如 systemd 支持、部分内核行为、路径习惯）。
- **虚拟机**里装一个 **Ubuntu Server**（或你目标发行版），更接近生产机，适合做：
  - 完整 Nginx 配置与反向代理测试
  - systemd 服务安装与启停
  - 读 `/etc`、进程列表、权限等系统调用在「真机」上的行为
  - 部署脚本、一键安装脚本的验证

### 3.2 虚拟机方案选型

| 方式 | 说明 |
|------|------|
| **Hyper-V** | Windows 专业版/企业版自带，与 WSL2 共用虚拟化，需在「启用或关闭 Windows 功能」里开启 Hyper-V。 |
| **VirtualBox** | 免费，与 WSL2 同时用时可能需关闭 Hyper-V 或做网络配置。 |
| **VMware Workstation** | 功能强，与 Hyper-V 二选一。 |

任选其一即可；若本机已开 WSL2，用 **Hyper-V + 第二代虚拟机** 装 Ubuntu Server 较省事。

### 3.3 在虚拟机里准备「测试用 Linux」

1. 下载 **Ubuntu Server** ISO：<https://ubuntu.com/download/server>。
2. 在 Hyper-V / VirtualBox / VMware 中新建虚拟机，挂载该 ISO，安装 Ubuntu Server（建议 22.04 LTS）。
3. 安装时可选：OpenSSH，便于从 Windows 用 SSH 连进去操作。
4. 装好后在虚机内安装常用依赖，例如：
   ```bash
   sudo apt update
   sudo apt install -y python3 python3-venv python3-pip nginx mysql-server git
   ```
5. 若希望从 Windows 访问虚机里的服务，配置「桥接网络」或「端口转发」，使 Windows 能访问虚机 IP（如 `http://<虚机IP>:8000`）。

### 3.4 把代码放到虚拟机里测试的两种方式

- **方式 A：从 Windows 拷贝过去**  
  用 `scp`、`rsync` 或共享文件夹，把 `PanelLab` 目录同步到虚机，在虚机里建 venv、装依赖、跑 uvicorn 和前端构建产物，做一次完整集成测试。

- **方式 B：在虚机里用 Git 拉取**  
  若代码已在 Git 仓库，在虚机里 `git clone`，然后同上述步骤运行。适合「定期从 main 拉取再测」的节奏。

---

## 四、何时用 WSL、何时用虚拟机（小结）

- **日常开发与调试**：用 **WSL2** —— 改代码即生效、启动快、和 Cursor 无缝。
- **涉及系统调用的功能**：在 WSL 里先做接口 + Mock 或真实调用（WSL 也是 Linux），能发现大部分问题。
- **阶段性/发版前**：在 **虚拟机 Linux** 上做一次「完整环境」集成测试：装 Nginx、systemd、真实路径与权限，确认无路径/换行符/权限等问题。

---

## 五、系统调用抽象与 Mock 建议（对应 README）

README 提到：*「涉及系统调用的部分尽量抽象成接口，在开发机用 Mock 或 WSL 测。」*

- **抽象**：例如「执行 shell」「读 /etc/xxx」「进程列表」不要在后端里到处写死 `subprocess`/`open('/etc/...')`，而是封装成少量接口（如 `SystemExecutor`、`ConfigReader`），由依赖注入或配置决定实现。
- **在开发机**：实现一个 **Mock** 实现（返回固定数据），便于在纯 Windows 或未装 Nginx 的 WSL 里跑通业务逻辑。
- **在 WSL**：可换用「真实实现」，在 WSL 里测到真实 shell、真实文件系统。
- **在虚拟机**：用同一套真实实现，做最终集成测试，确保 Nginx、systemd、路径等在「真 Linux」上无误。

这样即可做到：**写代码在 Windows，跑与调试在 WSL，定期在虚拟机 Linux 上做一次集成测试**。

---

## 六、快速检查清单

- [ ] WSL2 已安装，项目路径在 WSL 中可访问（如 `/mnt/e/Dev/Projects/PanelLab`）。
- [ ] 在 Cursor 中通过「Connect to WSL」在 WSL 里打开项目并运行后端。
- [ ] 后端在 WSL 中 `uvicorn ... --host 0.0.0.0`，Windows 浏览器能访问 `http://localhost:8000`。
- [ ] （可选）虚拟机已装好 Ubuntu Server，并安装 Python/Nginx/MySQL，能从中运行 PanelLab 或访问其服务。
- [ ] 系统相关逻辑已抽象为接口，并在 WSL/虚拟机中用过「真实实现」做测试。

按上述步骤即可用 **WSL + 虚拟机** 完成 Linux 端开发与测试；若你希望把某一段（如 Hyper-V 详细步骤或 Cursor 的 launch.json 示例）展开成单独小节，可以说明你的环境（Hyper-V/VirtualBox/VMware），我可以按你的环境再写一版操作步骤。
