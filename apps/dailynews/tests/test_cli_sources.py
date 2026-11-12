import json

from conftest import fixture_bytes, raw_settings
from dailynews import cli


def _write(tmp_path, sources):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps(raw_settings(sources)), encoding="utf-8")
    return str(p)


def test_check_sources_all_ok_exits_0(tmp_path, http_server, no_politeness, capsys):
    http_server.route("/a.xml", body=fixture_bytes("bbc_world.xml"))
    http_server.route("/b.xml", body=fixture_bytes("atom_sample.xml"))
    path = _write(tmp_path, [
        {"id": "a", "url": http_server.url("/a.xml"), "kind": "rss"},
        {"id": "b", "url": http_server.url("/b.xml"), "kind": "atom"},
        {"id": "off", "url": http_server.url("/nope.xml"), "kind": "rss", "enabled": False},
    ])
    assert cli.main(["--settings", path, "check-sources"]) == 0
    out = capsys.readouterr().out
    assert "a" in out and "3" in out and "b" in out and "2" in out
    assert "off" not in out


def test_check_sources_failure_exits_3(tmp_path, http_server, no_politeness, capsys):
    http_server.route("/a.xml", body=fixture_bytes("bbc_world.xml"))
    http_server.route("/bad.xml", status=503)
    http_server.route("/broken.xml", body=fixture_bytes("broken.xml"))
    path = _write(tmp_path, [
        {"id": "a", "url": http_server.url("/a.xml"), "kind": "rss"},
        {"id": "bad", "url": http_server.url("/bad.xml"), "kind": "rss"},
        {"id": "broken", "url": http_server.url("/broken.xml"), "kind": "rss"},
    ])
    assert cli.main(["--settings", path, "check-sources"]) == 3
    out = capsys.readouterr().out
    assert "bad" in out and "503" in out
    assert "broken" in out


def test_check_sources_invalid_settings_exits_2(tmp_path, capsys):
    p = tmp_path / "s.json"
    p.write_text("{}", encoding="utf-8")
    assert cli.main(["--settings", str(p), "check-sources"]) == 2
