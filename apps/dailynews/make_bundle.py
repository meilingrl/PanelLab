#!/usr/bin/env python3
"""在 Mac 上打 DailyNews 安装包：python3 apps/dailynews/make_bundle.py [--out-dir DIR]

输出 dist/dailynews-<版本>.tar.gz（默认在本文件旁的 dist/）。确定性：同一份源码两次打包字节相同
（固定顺序、mtime 0、属主 root、gzip 头不含时间和文件名）。不含 tests、__pycache__、*.pyc。
"""
import argparse
import gzip
import io
import os
import re
import sys
import tarfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOP_FILES = ("install.sh", "default_settings.json", "README.md")
PACKAGE = "dailynews"
SKIP_DIRS = ("__pycache__",)
SKIP_SUFFIXES = (".pyc", ".pyo")


def read_version():
    with open(os.path.join(HERE, PACKAGE, "__init__.py"), encoding="utf-8") as f:
        m = re.search(r'^__version__ = "([^"]+)"', f.read(), re.M)
    if not m:
        raise SystemExit("无法从 dailynews/__init__.py 读出版本号")
    return m.group(1)


def _entries():
    """(相对路径, 是否目录)，按路径排序。"""
    out = [(name, False) for name in TOP_FILES]
    out.append((PACKAGE, True))
    for root, dirs, files in os.walk(os.path.join(HERE, PACKAGE)):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        rel = os.path.relpath(root, HERE)
        for d in dirs:
            out.append((os.path.join(rel, d), True))
        for f in files:
            if f.endswith(SKIP_SUFFIXES) or f.startswith("."):
                continue
            out.append((os.path.join(rel, f), False))
    return sorted(out)


def _info(name, isdir, size=0, executable=False):
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE if isdir else tarfile.REGTYPE
    info.mode = 0o755 if isdir or executable else 0o644
    info.size = size
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    return info


def build_bundle(out_dir=None):
    version = read_version()
    top = "dailynews-%s" % version
    out_dir = out_dir or os.path.join(HERE, "dist")
    os.makedirs(out_dir, exist_ok=True)

    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        tar.addfile(_info(top, True))
        for rel, isdir in _entries():
            arc = "%s/%s" % (top, rel.replace(os.sep, "/"))
            if isdir:
                tar.addfile(_info(arc, True))
                continue
            with open(os.path.join(HERE, rel), "rb") as f:
                data = f.read()
            tar.addfile(_info(arc, False, len(data), executable=rel == "install.sh"), io.BytesIO(data))

    path = os.path.join(out_dir, top + ".tar.gz")
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        with gzip.GzipFile(filename="", mode="wb", fileobj=f, mtime=0, compresslevel=9) as gz:
            gz.write(raw.getvalue())
    os.replace(tmp, path)
    return path


def main(argv=None):
    p = argparse.ArgumentParser(description="打 DailyNews 安装包")
    p.add_argument("--out-dir", default=None, help="输出目录（默认 apps/dailynews/dist）")
    args = p.parse_args(argv)
    path = build_bundle(args.out_dir)
    print(path)
    print("上传：scp %s <你的用户名>@<你的服务器>:~/" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
