#!/bin/bash
# DailyNews installer. Run from the unpacked bundle directory, after the PanelLab Agent is installed:
#
#   sudo bash install.sh              install or upgrade the program; keep settings, data and site
#   sudo bash install.sh --uninstall  remove the program only; keep settings, data and site
#
# Layout: program /srv/dailynews/app (root, world-readable), settings /srv/dailynews/settings.json
# (panellab, 0640, created only when absent), data /var/lib/dailynews (panellab, 0750),
# site /srv/dailynews/site (panellab, 0755). The web server config is left to the user.
set -eu

ACCOUNT="panellab"
BASE="/srv/dailynews"
APP="$BASE/app"
SITE="$BASE/site"
SETTINGS="$BASE/settings.json"
DATA_DIR="/var/lib/dailynews"
PYTHON="/usr/bin/python3"

SRC="$(cd "$(dirname "$0")" && pwd)"
# Every python3 started below puts the current directory first on sys.path; run from /
# so nothing in the bundle or the caller's directory can be imported as root.
cd /

say() { printf '%s\n' "$*"; }
die() { printf '错误：%s\n' "$*" >&2; exit 1; }

usage() {
    say "用法：sudo bash install.sh [--uninstall]"
    exit 2
}

MODE="install"
for arg in "$@"; do
    case "$arg" in
        --uninstall) MODE="uninstall" ;;
        -h|--help) usage ;;
        *) printf '未知参数：%s\n' "$arg" >&2; usage ;;
    esac
done

[ "$(id -u)" = "0" ] || die "请用 sudo 运行：sudo bash install.sh"
getent passwd "$ACCOUNT" >/dev/null || die "找不到用户 $ACCOUNT：请先安装 PanelLab Agent，再安装 DailyNews"

# ------------------------------------------------------------------ uninstall
if [ "$MODE" = "uninstall" ]; then
    rm -rf "$APP"
    say "已删除程序 $APP"
    say "保留设置 $SETTINGS、数据 $DATA_DIR 和网站 $SITE；需要时请自行删除"
    say "在 Agent 页面删除 dailynews-build、dailynews-notify 两个作业，否则它们会按时运行并失败"
    exit 0
fi

# -------------------------------------------------------------------- install
[ -x "$PYTHON" ] || die "未找到 $PYTHON"
"$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3,9))' \
    || die "需要 Python 3.9 或更高版本（$PYTHON 版本过低）"
"$PYTHON" -c 'import zoneinfo; zoneinfo.ZoneInfo("Asia/Shanghai")' >/dev/null 2>&1 \
    || die "系统缺少时区数据（Asia/Shanghai），请先运行 sudo apt install tzdata"
[ -f "$SRC/dailynews/__init__.py" ] || die "安装包不完整：缺少 $SRC/dailynews"
[ -d "$SRC/dailynews/templates" ] || die "安装包不完整：缺少 $SRC/dailynews/templates"
[ -f "$SRC/default_settings.json" ] || die "安装包不完整：缺少 $SRC/default_settings.json"

VERSION="$(sed -n 's/^__version__ *= *["'"'"']\([^"'"'"']*\)["'"'"'].*$/\1/p' "$SRC/dailynews/__init__.py" | head -n 1)"
case "$VERSION" in
    ""|*[!A-Za-z0-9._+-]*|.*) die "无法从 dailynews/__init__.py 读出合法的版本号" ;;
esac
say "安装 DailyNews $VERSION"

install -d -o root -g root -m 0755 "$BASE"
install -d -o "$ACCOUNT" -g "$ACCOUNT" -m 0755 "$SITE"
install -d -o "$ACCOUNT" -g "$ACCOUNT" -m 0750 "$DATA_DIR"

# Stage next to the final place (same filesystem), then swap in with two renames.
STAGE="$BASE/.app-stage.$$"
OLD="$BASE/.app-old.$$"
cleanup() {
    # Died between the two renames: put the previous copy back instead of losing it.
    if [ ! -e "$APP" ] && [ -d "$OLD" ]; then
        mv -T "$OLD" "$APP" || true
    fi
    rm -rf "$STAGE" "$OLD"
}
trap cleanup EXIT

rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -R "$SRC/dailynews" "$STAGE/"
cp "$SRC/default_settings.json" "$STAGE/"
find "$STAGE" -name '__pycache__' -prune -exec rm -rf {} +
find "$STAGE" \( -name '*.pyc' -o -name '*.pyo' \) -exec rm -f {} +
"$PYTHON" -m compileall -q "$STAGE/dailynews" >/dev/null
chown -R root:root "$STAGE"
find "$STAGE" -type d -exec chmod 0755 {} +
find "$STAGE" -type f -exec chmod 0644 {} +
if [ -e "$APP" ]; then
    mv -T "$APP" "$OLD"
fi
mv -T "$STAGE" "$APP"
rm -rf "$OLD"
say "已安装程序到 $APP"

if [ -e "$SETTINGS" ] || [ -L "$SETTINGS" ]; then
    say "保留已有设置文件 $SETTINGS（不覆盖）"
else
    install -o "$ACCOUNT" -g "$ACCOUNT" -m 0640 "$SRC/default_settings.json" "$SETTINGS"
    say "已创建设置文件 $SETTINGS（来自默认设置）"
fi

say "以 $ACCOUNT 身份校验设置："
if command -v runuser >/dev/null 2>&1; then
    as_account() { runuser -u "$ACCOUNT" -- "$@"; }
else
    as_account() { sudo -u "$ACCOUNT" -- "$@"; }
fi
if ! (cd "$APP" && as_account "$PYTHON" -m dailynews --settings "$SETTINGS" check-settings); then
    die "设置校验没有通过：程序已安装，请按上面的提示修改设置文件后重新校验"
fi

cat <<'JOBS'

下一步：在 PanelLab 的 Agent 页面新建下面两个作业（字段照填；秘密环境变量填你自己的值）。

作业 1
  作业 ID       "dailynews-build"
  名称          DailyNews 生成
  命令          ["/usr/bin/python3","-m","dailynews","build"]
  工作目录      /srv/dailynews/app
  计划          "20 23 * * *"   时区 Asia/Shanghai
  超时          1800 秒
  重试          2 次，间隔 600 秒
  补跑窗口      60 分钟
  秘密环境变量  DAILYNEWS_LLM_API_KEY = <你的 DeepSeek API 密钥>

作业 2
  作业 ID       "dailynews-notify"
  名称          DailyNews 提醒
  命令          ["/usr/bin/python3","-m","dailynews","notify"]
  工作目录      /srv/dailynews/app
  计划          "0 0 * * *"   时区 Asia/Shanghai
  超时          120 秒
  重试          1 次，间隔 300 秒
  补跑窗口      60 分钟
  秘密环境变量  DAILYNEWS_SMTP_PASSWORD = <你的邮箱 SMTP 授权码>

建好后对 dailynews-build 点「立即运行」，完整步骤见 docs/user/dailynews.md。
JOBS
