import copy
import json
import os
import subprocess
import sys

import pytest

from dailynews.settings import SettingsError, load_settings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT = os.path.join(ROOT, "default_settings.json")


def _raw():
    with open(DEFAULT, encoding="utf-8") as f:
        return json.load(f)


def _write(tmp_path, data):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(p)


def test_default_settings_are_valid():
    s = load_settings(DEFAULT)
    assert len(s.sources) >= 20
    cat_ids = {c.id for c in s.categories}
    assert cat_ids == {"politics", "world", "tech", "ai", "finance"}
    for src in s.sources:
        assert src.category in cat_ids
        assert src.type in ("agency", "media", "official", "company")
        assert src.kind in ("rss", "atom", "html")
    assert s.timezone == "Asia/Shanghai"
    assert s.site_url == "https://news.panellab.cn"
    assert s.llm.model == "deepseek-chat"


def test_unknown_category_rejected(tmp_path):
    d = _raw()
    d["sources"][0]["category"] = "nope"
    with pytest.raises(SettingsError) as e:
        load_settings(_write(tmp_path, d))
    assert e.value.field == "sources[0].category"


def test_non_http_url_rejected(tmp_path):
    d = _raw()
    d["sources"][3]["url"] = "ftp://example.com/feed"
    with pytest.raises(SettingsError) as e:
        load_settings(_write(tmp_path, d))
    assert str(e.value) == "sources[3].url: 必须是 http 或 https 地址"


def test_duplicate_source_id_rejected(tmp_path):
    d = _raw()
    d["sources"][1]["id"] = d["sources"][0]["id"]
    with pytest.raises(SettingsError) as e:
        load_settings(_write(tmp_path, d))
    assert e.value.field == "sources[1].id"


def test_bad_timezone_rejected(tmp_path):
    d = _raw()
    d["timezone"] = "Mars/Base"
    with pytest.raises(SettingsError) as e:
        load_settings(_write(tmp_path, d))
    assert e.value.field == "timezone"


@pytest.mark.parametrize("key", ["api_key", "password"])
def test_secrets_not_allowed_in_file(tmp_path, key):
    d = copy.deepcopy(_raw())
    section = "llm" if key == "api_key" else "smtp"
    d[section][key] = "x"
    with pytest.raises(SettingsError) as e:
        load_settings(_write(tmp_path, d))
    assert "环境变量" in str(e.value)


def test_check_settings_cli_exit_codes(tmp_path):
    def run(path):
        return subprocess.run(
            [sys.executable, "-m", "dailynews", "--settings", path, "check-settings"],
            cwd=ROOT, capture_output=True, text=True,
        )

    ok = run(DEFAULT)
    assert ok.returncode == 0
    assert ok.stdout.startswith("设置有效：")
    d = _raw()
    d["timezone"] = "Mars/Base"
    bad = run(_write(tmp_path, d))
    assert bad.returncode == 2
    assert "timezone" in bad.stdout + bad.stderr
