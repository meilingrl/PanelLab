"""install.sh 的静态检查与 make_bundle.py 的确定性打包。安装行为本身由 scripts/verify_agent_lab.py --dailynews 验证。"""
import hashlib
import importlib.util
import io
import os
import re
import tarfile

import pytest

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALL = os.path.join(APP, "install.sh")


@pytest.fixture(scope="module")
def script():
    with open(INSTALL, encoding="utf-8") as f:
        return f.read()


def _code_lines(text):
    """去掉空行和整行注释（含 shebang）后的行。"""
    return [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


def test_starts_with_set_eu(script):
    assert script.startswith("#!/bin/bash\n")
    assert _code_lines(script)[0] == "set -eu"


def test_requires_root(script):
    assert re.search(r'\[ "\$\(id -u\)" = "0" \] \|\| die ', script)


def test_requires_existing_panellab_account(script):
    assert 'ACCOUNT="panellab"' in script
    assert re.search(r'getent passwd "\$ACCOUNT" >/dev/null \|\| die ', script)
    # 只检查账户，不创建账户（账户由 Agent 安装程序负责）
    assert "useradd" not in script and "usermod" not in script and "groupadd" not in script


def test_never_mentions_nginx(script):
    assert "nginx" not in script.lower()


def test_never_touches_old_deployment(script):
    assert "newsbot" not in script and "news-briefing" not in script


def test_settings_only_written_when_absent(script):
    lines = script.splitlines()
    writes = [i for i, ln in enumerate(lines) if '"$SETTINGS"' in ln and "default_settings.json" in ln]
    assert len(writes) == 1, "只能有一处写设置文件"
    i = writes[0]
    # 写入发生在“设置文件已存在则保留”的分支之后的 else 里
    guard = "\n".join(lines[max(0, i - 4):i])
    assert re.search(r'if \[ -e "\$SETTINGS" \] \|\| \[ -L "\$SETTINGS" \]; then', guard), guard
    assert "else" in lines[i - 1]
    assert "-m 0640" in lines[i] and "-o \"$ACCOUNT\" -g \"$ACCOUNT\"" in lines[i]
    # 其它地方不能 rm/mv/cp 到设置文件
    for k, ln in enumerate(lines):
        if k != i and "$SETTINGS" in ln:
            assert not re.search(r"\b(rm|mv|cp|install|tee)\b|>", ln.split("#")[0]), ln


def test_uninstall_keeps_data_settings_and_site(script):
    m = re.search(r'if \[ "\$MODE" = "uninstall" \]; then\n(.*?)\n    exit 0\nfi', script, re.S)
    assert m, "找不到卸载分支"
    body = m.group(1)
    removed = re.findall(r"rm -rf (.+)", body)
    assert removed == ['"$APP"'], removed


def test_check_settings_runs_as_panellab(script):
    assert 'as_account() { runuser -u "$ACCOUNT" -- "$@"; }' in script
    assert 'as_account "$PYTHON" -m dailynews --settings "$SETTINGS" check-settings' in script


def test_prints_both_jobs(script):
    for needle in ('"dailynews-build"', '"dailynews-notify"', '"20 23 * * *"', '"0 0 * * *"',
                   "Asia/Shanghai", "DAILYNEWS_LLM_API_KEY", "DAILYNEWS_SMTP_PASSWORD",
                   '["/usr/bin/python3","-m","dailynews","build"]',
                   '["/usr/bin/python3","-m","dailynews","notify"]'):
        assert needle in script, needle


# ---------- make_bundle ----------

def _load_make_bundle():
    spec = importlib.util.spec_from_file_location("make_bundle", os.path.join(APP, "make_bundle.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_bundle_is_deterministic_and_complete(tmp_path):
    mb = _load_make_bundle()
    # 先在包里留一个缓存文件，打包时必须跳过
    cache = os.path.join(APP, "dailynews", "__pycache__")
    os.makedirs(cache, exist_ok=True)
    junk = os.path.join(cache, "zz_bundle_test.cpython-39.pyc")
    with open(junk, "wb") as f:
        f.write(b"junk")
    try:
        a = mb.build_bundle(str(tmp_path / "a"))
        b = mb.build_bundle(str(tmp_path / "b"))
    finally:
        os.remove(junk)
    with open(a, "rb") as f:
        data_a = f.read()
    with open(b, "rb") as f:
        data_b = f.read()
    assert os.path.basename(a) == "dailynews-%s.tar.gz" % mb.read_version()
    assert hashlib.sha256(data_a).digest() == hashlib.sha256(data_b).digest()

    with tarfile.open(fileobj=io.BytesIO(data_a), mode="r:gz") as tar:
        members = tar.getmembers()
    names = [m.name for m in members]
    top = "dailynews-%s" % mb.read_version()
    assert names == sorted(names)
    assert all(n == top or n.startswith(top + "/") for n in names)
    assert not any("/tests" in n or "__pycache__" in n or n.endswith((".pyc", ".pyo")) or "/dist" in n
                   for n in names), names
    for need in ("install.sh", "default_settings.json", "README.md", "dailynews/__init__.py",
                 "dailynews/__main__.py", "dailynews/cli.py", "dailynews/templates/page.html",
                 "dailynews/templates/archive.html", "dailynews/templates/style.css",
                 "dailynews/templates/app.js"):
        assert "%s/%s" % (top, need) in names, need
    for m in members:
        assert (m.uid, m.gid, m.uname, m.gname, m.mtime) == (0, 0, "root", "root", 0), m.name
        if m.isdir():
            assert m.mode == 0o755, m.name
        elif m.name.endswith("/install.sh"):
            assert m.mode == 0o755
        else:
            assert m.isfile() and m.mode == 0o644, m.name
