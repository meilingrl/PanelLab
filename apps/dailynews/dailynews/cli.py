"""命令行入口：python -m dailynews --settings PATH <command>。"""
import argparse
import sys

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
    for name, helptext in (("build", "采集并生成当期简报"),
                           ("notify", "发送当期提醒邮件")):
        sub.add_parser(name, help=helptext).set_defaults(func=_not_implemented(name))
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)
