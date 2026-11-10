#!/bin/bash
# PanelLab Agent installer. Run from the unpacked bundle directory:
#
#   sudo bash install.sh                      install or upgrade (drains first)
#   sudo bash install.sh --force              upgrade, cancelling running jobs
#   sudo bash install.sh --uninstall          stop the service, remove program and unit; keep data
#   sudo bash install.sh --uninstall --purge  also delete /var/lib/panellab-agent and /etc/panellab-agent
#
# Idempotent: running it again with the same version only restarts the service.
# It never touches the existing account named "agent".
set -eu

ACCOUNT="panellab"
PREFIX="/opt/panellab-agent"
DATA_DIR="/var/lib/panellab-agent"
CONF_DIR="/etc/panellab-agent"
SERVICE="panellab-agent.service"
UNIT_DST="/etc/systemd/system/panellab-agent.service"
CTL="/usr/local/bin/panellab-agentctl"
PYTHON="/usr/bin/python3"
SOCKET="/run/panellab-agent/agent.sock"
RELOGIN_NOTE="请重新登录 SSH 使组权限生效"

SRC="$(cd "$(dirname "$0")" && pwd)"

say() { printf '%s\n' "$*"; }
die() { printf '错误：%s\n' "$*" >&2; exit 1; }

usage() {
    say "用法：sudo bash install.sh [--force] | --uninstall [--purge] [--force]"
    exit 2
}

MODE="install"
PURGE=0
FORCE=0
for arg in "$@"; do
    case "$arg" in
        --uninstall) MODE="uninstall" ;;
        --purge) PURGE=1 ;;
        --force) FORCE=1 ;;
        -h|--help) usage ;;
        *) printf '未知参数：%s\n' "$arg" >&2; usage ;;
    esac
done
if [ "$PURGE" = 1 ] && [ "$MODE" != "uninstall" ]; then
    printf '%s\n' "--purge 只能与 --uninstall 一起使用" >&2
    usage
fi

[ "$(id -u)" = "0" ] || die "请用 sudo 运行：sudo bash install.sh"
[ -d /run/systemd/system ] || die "本机没有以 systemd 运行（/run/systemd/system 不存在）"

service_active() { systemctl is-active --quiet "$SERVICE"; }

write_ctl_wrapper() {
    install -d -o root -g root -m 0755 "$(dirname "$CTL")"
    local tmp="$CTL.tmp.$$"
    cat > "$tmp" <<'WRAPPER'
#!/bin/sh
exec env PYTHONPATH=/opt/panellab-agent/current /usr/bin/python3 -m panellab_agent.ctl "$@"
WRAPPER
    chown root:root "$tmp"
    chmod 0755 "$tmp"
    mv -f "$tmp" "$CTL"
}

# Ask the running agent to stop starting runs and wait for the running ones.
drain_running_agent() {
    service_active || return 0
    [ -x "$CTL" ] || write_ctl_wrapper
    say "服务正在运行：先排空（不再启动新运行，等待正在运行的作业结束）……"
    printf '%s\n' '{"protocol_version":1,"op":"status","args":{}}' \
        | "$CTL" 2>/dev/null \
        | "$PYTHON" -c '
import json, sys
try:
    running = json.loads(sys.stdin.readline())["result"]["running"]
except Exception:
    sys.exit(0)
for r in running:
    print("  等待：作业 %s 的运行 %s（开始于 %s）" % (r.get("job_id"), r.get("run_id"), r.get("started_at")))
if not running:
    print("  没有正在运行的作业")
' || true
    if [ "$FORCE" = 1 ]; then
        say "  --force：取消正在运行的作业"
        set -- drain --force
    else
        set -- drain
    fi
    if ! "$CTL" "$@" >/dev/null; then
        say "排空没有完成。Agent 仍处于排空状态（不会启动新运行）。" >&2
        say "可以加 --force 重新执行以取消正在运行的作业，或执行 systemctl restart $SERVICE 恢复运行。" >&2
        exit 1
    fi
    say "排空完成"
}

# ------------------------------------------------------------------ uninstall
if [ "$MODE" = "uninstall" ]; then
    if [ -e "$UNIT_DST" ] || systemctl cat "$SERVICE" >/dev/null 2>&1; then
        drain_running_agent
        systemctl disable --now "$SERVICE" >/dev/null 2>&1 || true
        rm -f "$UNIT_DST"
        systemctl daemon-reload
        systemctl reset-failed "$SERVICE" >/dev/null 2>&1 || true
        say "已停止并删除服务 $SERVICE"
    else
        say "服务 $SERVICE 未安装"
    fi
    rm -rf "$PREFIX"
    rm -f "$CTL"
    say "已删除程序 $PREFIX 和 $CTL"
    if [ "$PURGE" = 1 ]; then
        rm -rf "$DATA_DIR" "$CONF_DIR"
        say "已删除数据 $DATA_DIR 和配置 $CONF_DIR"
    else
        say "保留数据 $DATA_DIR 和配置 $CONF_DIR（加 --purge 可一并删除）"
    fi
    say "系统账户 $ACCOUNT 保留未删除"
    exit 0
fi

# -------------------------------------------------------------------- install
[ -x "$PYTHON" ] || die "未找到 $PYTHON"
"$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3,9))' \
    || die "需要 Python 3.9 或更高版本（$PYTHON 版本过低）"
[ -f "$SRC/panellab_agent/__init__.py" ] || die "安装包不完整：缺少 $SRC/panellab_agent"
[ -f "$SRC/systemd/panellab-agent.service" ] || die "安装包不完整：缺少 $SRC/systemd/panellab-agent.service"

VERSION="$(sed -n 's/^__version__ *= *["'"'"']\([^"'"'"']*\)["'"'"'].*$/\1/p' "$SRC/panellab_agent/__init__.py" | head -n 1)"
case "$VERSION" in
    ""|*[!A-Za-z0-9._+-]*|.*) die "无法从 panellab_agent/__init__.py 读出合法的版本号" ;;
esac
say "安装 PanelLab Agent $VERSION"

# Account and groups.
NOLOGIN="/usr/sbin/nologin"
[ -x "$NOLOGIN" ] || NOLOGIN="/sbin/nologin"
[ -x "$NOLOGIN" ] || NOLOGIN="/bin/false"
if getent passwd "$ACCOUNT" >/dev/null; then
    shell="$(getent passwd "$ACCOUNT" | cut -d: -f7)"
    case "$shell" in
        /usr/sbin/nologin|/sbin/nologin|/bin/false|/usr/bin/false) ;;
        *) die "已存在用户 $ACCOUNT，且其登录 shell 为 $shell。它看起来不是 Agent 的系统账户，安装程序不会改用它；请先确认该账户的用途" ;;
    esac
    [ "$(id -gn "$ACCOUNT")" = "$ACCOUNT" ] \
        || die "已存在用户 $ACCOUNT，但其主组不是 $ACCOUNT；安装程序不会改动它"
    say "系统账户 $ACCOUNT 已存在"
else
    getent group "$ACCOUNT" >/dev/null || groupadd --system "$ACCOUNT"
    useradd --system --gid "$ACCOUNT" --home-dir "$DATA_DIR" --no-create-home \
        --shell "$NOLOGIN" --comment "PanelLab Agent" "$ACCOUNT"
    say "已创建系统账户 $ACCOUNT（无登录 shell）"
fi

in_group() { id -nG "$1" | tr ' ' '\n' | grep -qx "$2"; }

if getent group adm >/dev/null; then
    if in_group "$ACCOUNT" adm; then
        say "$ACCOUNT 已在 adm 组"
    else
        usermod -a -G adm "$ACCOUNT"
        say "已把 $ACCOUNT 加入 adm 组（用于读取 SSH 登录失败记录）"
    fi
else
    say "注意：本机没有 adm 组，SSH 登录失败检查可能读不到日志"
fi

INSTALLER="${SUDO_USER:-}"
if [ -z "$INSTALLER" ] || [ "$INSTALLER" = "root" ]; then
    say "注意：未检测到执行 sudo 的普通用户，跳过加入 $ACCOUNT 组；需要时用 usermod -a -G $ACCOUNT <用户名> 自行加入"
elif [ "$INSTALLER" = "agent" ]; then
    say "注意：不改动已有账户 agent，跳过加入 $ACCOUNT 组"
elif ! getent passwd "$INSTALLER" >/dev/null; then
    say "注意：找不到用户 $INSTALLER，跳过加入 $ACCOUNT 组"
elif in_group "$INSTALLER" "$ACCOUNT"; then
    say "$INSTALLER 已在 $ACCOUNT 组"
else
    usermod -a -G "$ACCOUNT" "$INSTALLER"
    say "已把 $INSTALLER 加入 $ACCOUNT 组"
fi

# Directories.
install -d -o root -g root -m 0755 "$PREFIX"
install -d -o "$ACCOUNT" -g "$ACCOUNT" -m 0750 "$DATA_DIR"
install -d -o "$ACCOUNT" -g "$ACCOUNT" -m 0750 "$CONF_DIR"

# Stage the program next to its final place so the later moves stay on one filesystem.
STAGE="$PREFIX/.stage-$VERSION.$$"
cleanup() { rm -rf "$STAGE" "$PREFIX/.old-$VERSION.$$" "$PREFIX/.current.$$"; }
trap cleanup EXIT
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -R "$SRC/panellab_agent" "$STAGE/"
find "$STAGE" -name '__pycache__' -prune -exec rm -rf {} +
find "$STAGE" \( -name '*.pyc' -o -name '*.pyo' \) -exec rm -f {} +

VDIR="$PREFIX/$VERSION"
SAME=0
if [ -d "$VDIR" ] && diff -r -q --exclude='__pycache__' "$STAGE" "$VDIR" >/dev/null 2>&1; then
    SAME=1
fi
CURRENT_TARGET="$(readlink "$PREFIX/current" 2>/dev/null || true)"
if [ "$SAME" = 1 ] && [ "$CURRENT_TARGET" = "$VERSION" ]; then
    say "版本 $VERSION 已安装且文件相同，只重启服务"
fi

write_ctl_wrapper
drain_running_agent

if [ "$SAME" = 1 ]; then
    rm -rf "$STAGE"
else
    "$PYTHON" -m compileall -q "$STAGE/panellab_agent" >/dev/null
    chown -R root:root "$STAGE"
    find "$STAGE" -type d -exec chmod 0755 {} +
    find "$STAGE" -type f -exec chmod 0644 {} +
    chmod 0755 "$STAGE"
    if [ -e "$VDIR" ]; then
        mv -T "$VDIR" "$PREFIX/.old-$VERSION.$$"
    fi
    mv -T "$STAGE" "$VDIR"
    rm -rf "$PREFIX/.old-$VERSION.$$"
    say "已安装程序到 $VDIR"
fi

# Switch "current" atomically.
ln -sfn "$VERSION" "$PREFIX/.current.$$"
mv -T "$PREFIX/.current.$$" "$PREFIX/current"
say "current -> $VERSION"

install -o root -g root -m 0644 "$SRC/systemd/panellab-agent.service" "$UNIT_DST"
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null 2>&1
if service_active; then
    systemctl restart "$SERVICE"
    say "已重启服务 $SERVICE"
else
    systemctl reset-failed "$SERVICE" >/dev/null 2>&1 || true
    systemctl start "$SERVICE"
    say "已启动服务 $SERVICE"
fi

ok=0
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    if service_active && [ -S "$SOCKET" ]; then
        ok=1
        break
    fi
    sleep 0.5
done
if [ "$ok" != 1 ]; then
    systemctl --no-pager status "$SERVICE" >&2 || true
    die "服务没有正常启动，请查看 journalctl -u $SERVICE"
fi
say "服务已运行，控制 socket：$SOCKET"
say "命令行工具：$CTL"
say "$RELOGIN_NOTE"
