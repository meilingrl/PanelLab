"""Exercise the launcher without installing Docker or starting user services."""
import json
import os
from pathlib import Path
import subprocess
import sys

ENTRY = Path(__file__).resolve().parents[1] / "local_panel.py"


def launch(tmp_path, *args):
    return subprocess.run(
        [sys.executable, str(ENTRY), *args, "--state-dir", str(tmp_path / "state")],
        env={**os.environ, "PATH": str(tmp_path), "SECRET_KEY": "ambient-example"},
        capture_output=True, text=True, timeout=10,
    )


def test_retry_uses_the_same_private_credentials_after_build_failure(tmp_path):
    # Simulate a failing external build after first-run credentials have been prepared.
    docker = tmp_path / "docker"
    docker.write_text("#!/bin/sh\ncase \"$1\" in compose) exit 0;; build) exit 8;; esac\n")
    docker.chmod(0o700)
    initial = launch(tmp_path)
    assert initial.returncode == 8, initial.stderr
    state = tmp_path / "state"
    saved = state / "panellab-local.json"
    contents = saved.read_bytes()
    config = json.loads(contents)
    secrets = [config[key] for key in ("SECRET_KEY", "MYSQL_PASSWORD", "MYSQL_ROOT_PASSWORD", "INIT_ADMIN_PASSWORD")]
    assert len(set(secrets)) == 4
    assert all(len(value) >= 32 and value not in initial.stdout + initial.stderr for value in secrets)
    assert saved.stat().st_mode & 0o077 == 0
    assert state.stat().st_mode & 0o077 == 0
    assert launch(tmp_path).returncode == 8
    assert saved.read_bytes() == contents
    assert launch(tmp_path, "password").stdout.strip() == config["INIT_ADMIN_PASSWORD"]


def test_showing_password_before_setup_does_not_create_an_instance(tmp_path):
    result = launch(tmp_path, "password")
    assert result.returncode != 0
    assert "尚未初始化" in result.stderr
    assert not (tmp_path / "state").exists()


def test_stop_refuses_state_pointing_to_an_unrelated_compose_project(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    keys = ("MYSQL_PASSWORD", "MYSQL_ROOT_PASSWORD", "SECRET_KEY", "INIT_ADMIN_PASSWORD")
    config = {key: "a" * 48 for key in keys}
    (state / "panellab-local.json").write_text(json.dumps({**config, "project": "production"}))
    docker = tmp_path / "docker"
    docker.write_text("#!/bin/sh\necho 'docker invoked' >&2\nexit 0\n")
    docker.chmod(0o700)
    result = launch(tmp_path, "stop")
    assert result.returncode != 0
    assert "实例配置无效" in result.stderr
    assert "docker invoked" not in result.stderr
