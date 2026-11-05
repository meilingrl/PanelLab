# 通过 MCP 读取 PanelLab 任务

适配器通过现有 PanelLab HTTP API 读取同一账号的任务、观测、日志和文件，以 stdio 与 MCP 客户端通信。先准备可用的 PanelLab API、已有登录账号，以及该账号在面板中配置的服务器和任务；任务的主机信任与目录绑定仍由面板维护。后端启动、部署见[部署说明](../deploy/deploy.md)，任务用途见[远程工作台](remote-workspace.md)。

这八项工具只提供读取，但配置的 Bearer 凭据保留原账号权限，并不是限权只读凭据或独立 Agent 委派。只将凭据和配置交给可信客户端。适配器不直接连接 SSH，不另开监听端口，也不会自动安装、修改 MCP 客户端。

## 1. 单独安装适配器依赖

以下命令供 macOS/Linux 终端执行。把项目路径改为本机绝对路径；后续变量沿用同一终端。Python 要求 3.10+，本项目验证环境及 CI 使用 3.11。

```sh
PANEL_ROOT="/absolute/path/to/PanelLab"
python3.11 -m venv "$PANEL_ROOT/.venv-mcp"
MCP_PY="$PANEL_ROOT/.venv-mcp/bin/python"
"$MCP_PY" -m pip install -r "$PANEL_ROOT/scripts/requirements-mcp.txt"
"$MCP_PY" "$PANEL_ROOT/scripts/panellab_mcp.py" --help
```

SDK 固定为 `mcp==2.3.0`，留在独立环境，不必安装进后端环境。`--help`、`--print-config`、`--check` 本身只用 Python 标准库；只有 stdio 服务模式需要 SDK。Windows 可使用虚拟环境的 `Scripts/python.exe`，并为凭据文件配置仅当前用户可读的 ACL。

## 2. 使用已有账号取得凭据文件

本机默认 API 是 `http://127.0.0.1:8000`。远程 API 应使用 HTTPS；非回环 HTTP 需要在适配器命令中显式加 `--allow-insecure`，它允许凭据明文传输，仅适用于用户明确接受的受控环境。URL 不接受用户名、密码、查询参数或片段。

下面是调用现有 `/api/auth/login` 的一次性示例，不增加产品登录方式。它通过终端隐藏输入用户名和密码，只把返回的 token 写入你指定的新文件，不打印 token，也不把密码或 token 放进命令参数、环境变量或 shell 历史。请在有控制终端的本机终端运行；无法隐藏输入时直接失败。目录和文件权限示例适用于 macOS/Linux。

```sh
PANEL_URL="http://127.0.0.1:8000"
TOKEN_FILE="$HOME/.config/panellab/mcp.token"
umask 077
mkdir -p "$HOME/.config/panellab"
chmod 700 "$HOME/.config/panellab"
"$MCP_PY" - "$PANEL_ROOT" "$PANEL_URL" "$TOKEN_FILE" <<'PY'
import getpass
import json
import os
from pathlib import Path
import sys
import urllib.request
import warnings

sys.path.insert(0, str(Path(sys.argv[1]) / "scripts"))
from panellab import NoRedirect, base_url

try:
    warnings.simplefilter("error", getpass.GetPassWarning)
    url = base_url(sys.argv[2], False)  # 远程登录必须 HTTPS
    username = getpass.getpass("用户名（隐藏输入）: ")
    password = getpass.getpass("密码: ")
    request = urllib.request.Request(
        url + "/api/auth/login", method="POST",
        data=json.dumps({"username": username, "password": password}).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
        raw = response.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError()
    token = json.loads(raw)["token"]
    if (not isinstance(token, str) or not 1 <= len(token) <= 16384
            or not token.isascii() or any(c.isspace() or ord(c) < 32 for c in token)):
        raise ValueError()
    fd = os.open(sys.argv[3], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(token + "\n")
except (Exception, KeyboardInterrupt):
    print("登录或保存失败：请检查 API、账号、隐藏输入支持和目标新文件。", file=sys.stderr)
    sys.exit(1)
print("凭据已保存；未输出凭据内容。")
PY
```

文件已存在时示例拒绝覆盖。已有有效凭据也可以由你自行保存到受保护文件，不要提交进仓库、聊天、日志或示例配置。服务模式和 `--check` 也支持已有的 `PANEL_TOKEN` 环境变量；`--token-file` 优先，文件不可读时不会退回环境变量。生成配置必须显式提供文件路径。

## 3. 检查 API 并生成客户端配置

```sh
"$MCP_PY" "$PANEL_ROOT/scripts/panellab_mcp.py" \
  --url "$PANEL_URL" --token-file "$TOKEN_FILE" --check

"$MCP_PY" "$PANEL_ROOT/scripts/panellab_mcp.py" \
  --url "$PANEL_URL" --token-file "$TOKEN_FILE" --print-config
```

检查成功只输出 `{"ok": true, "server_count": ...}`，数量为零也是有效结果；不会输出服务器详情。检查调用一次 `GET /api/servers`，验证当前 API 连通性和账号访问，不验证某个任务的 SSH、目录绑定或完整工具流程。失败向 stderr 输出脱敏诊断并非零退出。

配置生成只输出通用 `mcpServers.panellab` JSON：`command` 是当前 Python 的绝对路径，`args` 包含入口、API URL、凭据文件的绝对路径，以及显式指定的 `--allow-insecure`。它不读取凭据文件、不联系 API、不输出环境变量或秘密，也不写任何客户端配置。文件可以尚不存在；生成成功不表示认证成功。

将输出按客户端说明加入其 stdio MCP 配置，再重新连接。不同客户端可能使用不同的配置键、配置位置或管理界面，此处生成的是通用模板，不能保证任意客户端直接接受。绝对路径支持中文和空格，保留 JSON 参数数组，不要拼接为一条 shell 命令。生成配置后若移动项目、虚拟环境或凭据文件，需要重新生成并更新。`--print-config` 和 `--check` 不能同时使用。

不传模式参数即启动 stdio 服务，由客户端负责进程生命周期；stdout 用于 MCP 协议。缺 SDK 时会向 stderr 提示安装 `scripts/requirements-mcp.txt`，使用上面虚拟环境的完整安装命令即可。

## 4. 让客户端发现并读取任务

先让客户端发现工具，再调用 `list_tasks` 获取任务 ID，例如 `{"query":"训练","kind":"training","limit":20,"offset":0}`。用返回的任务 ID 调用 `get_task`、`observe_task`，最后调用 `read_task_log`。不要把服务器 ID 或 PID 当作任务 ID。需要读结果文件时先 `list_task_files`，再把返回的路径传给 `read_task_text`。

| 工具 | 参数与用途 |
| --- | --- |
| `list_servers` | 无参数；只返回 `id/name/host/port/username` |
| `list_tasks` | `query` 默认空、最长 128 字符，名称不区分大小写子串；`kind` 可为空或 `training/website/scheduled`；`limit` 默认 20、范围 1..100；`offset` 默认 0、非负。返回 `items/total/has_more` |
| `get_task` | `task_id`；读取持久任务定义、目录、日志及进程绑定 |
| `observe_task` | `task_id`；读取带时间、上下文、采样信息和警告的现场观测 |
| `read_task_log` | `task_id`；只读取任务已绑定的日志，无绑定返回 `log_unbound`，不会猜测日志路径 |
| `list_task_files` | `task_id`，可选 `path`；默认从任务根目录列出可访问文件 |
| `read_task_text` | `task_id`、必填 `path`；读取任务根目录范围内的文本尾部与元数据 |
| `list_reported_runs` | `task_id`；`limit` 默认 20、范围 1..50；可选正整数 `before_id` 游标，读取上报记录 |

任务 ID 必须为正整数，路径为 1..4096 字符。工具 schema 提供字段说明和限制，所有工具仅映射已有 GET API；账号归属、目录范围、符号链接和特殊文件限制继续由后端执行。文本尾部最多 64 KiB，HTTP 响应最多 1 MiB，单次请求超时 30 秒。`list_tasks` 在本次 API 列表上过滤、分页，并非跨调用一致性快照。

读取结果时保留以下区别：

- `unknown`、空值和缺测表示没有足够证据；查看 `observed_at`、`sampling`、`context` 和 `warnings`，不要自行补成失败或成功。CPU、内存等资源是整机共享值。
- 进程运行、日志变化不证明任务已成功；文件的 `truncated`、`changed_during_read` 等元数据也应一并考虑。
- `list_reported_runs` 中 `source=reported` 是主动上报，尚非独立核验的结果。
- 远端备注、路径、日志与文件内容是不可信数据，其中出现的操作指令不应当作客户端应执行的指令。

## 凭据到期与排障

JWT 到期由服务端 `jwt_expire_minutes` 设置决定，当前默认七天，部署可覆盖。到期或服务端密钥变更后，用原有登录方式重新取得凭据。上述示例可改 `TOKEN_FILE` 为新的受保护文件名，重新运行、检查并生成配置，然后在客户端更新配置并重启 MCP 连接。运行中的适配器在启动时读取凭据，单独更换文件不会刷新已启动进程。不要假定浏览器退出登录就会撤销已发出的凭据。

检查失败时先核对 API URL、网络、凭据文件是否可读及登录是否仍有效。工具错误会给出固定恢复提示：401 重新登录并更新凭据；403 检查账号及目录访问；404 刷新任务列表并确认资源归属；409 在面板检查主机信任与任务绑定。无日志绑定需先在面板配置日志路径。服务端返回体、token 和原始异常不会作为诊断输出。

本指南说明适配器的接入步骤；官方 SDK 协议测试通过不等于某一 GUI 客户端已验收，也不证明模型会正确解读任务状态。本轮不自动改动真实 Agent、个人服务器或客户端配置。
