"""命令行入口：python -m dailynews --settings PATH <command>。"""
import argparse
import os
import sys
from datetime import datetime

from . import feeds, http
from .settings import SettingsError, load_settings

DEFAULT_SETTINGS_PATH = "/srv/dailynews/settings.json"


def _check_settings(args):
    try:
        s = load_settings(args.settings)
    except SettingsError as e:
        print("设置不合法：%s" % e)
        return 2
    print("设置有效：%d 个来源，%d 个类别" % (len(s.sources), len(s.categories)))
    return 0


def _check_sources(args):
    try:
        s = load_settings(args.settings)
    except SettingsError as e:
        print("设置不合法：%s" % e)
        return 2
    from datetime import datetime, timezone
    throttle = http.DomainThrottle()
    bad = 0
    for src in s.sources:
        if not src.enabled:
            continue
        try:
            throttle.wait(src.url)
            resp = http.get(src.url)
            if resp.status != 200:
                raise http.FetchError("HTTP %d" % resp.status)
            n = len(feeds.parse(src, resp.body, datetime.now(timezone.utc)))
            if n == 0:
                raise feeds.ParseError("没有解析出任何条目")
            print("%s\t可用\t%d" % (src.id, n))
        except (http.FetchError, feeds.ParseError) as e:
            bad += 1
            print("%s\t失败：%s\t0" % (src.id, e))
    return 3 if bad else 0


API_KEY_ENV = "DAILYNEWS_LLM_API_KEY"


def _iso_time(text):
    """--now：带时区偏移的 ISO 时间，例如 2026-11-12T23:20:00+08:00（也接受结尾的 Z）。"""
    t = text.strip()
    if t.endswith(("Z", "z")):
        t = t[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        raise argparse.ArgumentTypeError("不是合法的 ISO 时间：%s" % text)
    if dt.tzinfo is None:
        raise argparse.ArgumentTypeError("必须带时区偏移，例如 +08:00：%s" % text)
    return dt


def _build(args):
    try:
        s = load_settings(args.settings)
    except SettingsError as e:
        print("设置不合法：%s" % e)
        return 2
    api_key = os.environ.get(API_KEY_ENV, "").strip()
    if not api_key:
        print("缺少 %s" % API_KEY_ENV, file=sys.stderr)
        return 2
    from zoneinfo import ZoneInfo
    from .llm import LLM
    from .pipeline import run_build
    from .store import Store
    now = args.now or datetime.now().astimezone()
    stage = "准备"
    store = None
    try:
        os.makedirs(s.data_dir, exist_ok=True)
        store = Store(os.path.join(s.data_dir, "dailynews.db"))
        llm = LLM(s.llm, api_key, store, now.astimezone(ZoneInfo(s.timezone)).date())
        result = run_build(s, store, llm, now)
    except Exception as e:  # 只报阶段和异常类型，异常文字可能含路径或密钥
        print("构建失败（阶段：%s）：%s" % (stage, type(e).__name__))
        return 1
    finally:
        if store is not None:
            store.close()
    for line in result.lines:
        print(line)
    return result.exit_code


def _not_implemented(name):
    def run(args):
        print("%s 尚未实现" % name, file=sys.stderr)
        return 1
    return run


def build_parser():
    p = argparse.ArgumentParser(prog="dailynews", description="每日要闻简报")
    p.add_argument("--settings", default=DEFAULT_SETTINGS_PATH, help="设置文件路径")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("check-settings", help="校验设置文件").set_defaults(func=_check_settings)
    sub.add_parser("check-sources", help="检查各来源是否可访问").set_defaults(func=_check_sources)
    b = sub.add_parser("build", help="采集并生成当期简报")
    b.add_argument("--now", type=_iso_time, default=None,
                   help="把这个时间当作现在（ISO 时间，带时区；仅用于测试和补跑）")
    b.set_defaults(func=_build)
    sub.add_parser("notify", help="发送当期提醒邮件").set_defaults(func=_not_implemented("notify"))
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)
