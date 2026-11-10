"""Exercise the launcher without installing Docker or starting user services."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

ENTRY = Path(__file__).resolve().parents[1] / "local_panel.py"


def launch(tmp_path, *args, state_dir=None, **environment):
    return subprocess.run(
        [sys.executable, str(ENTRY), *args, "--state-dir", str(state_dir or tmp_path / "state")],
        env={**os.environ, "PATH": str(tmp_path), "SECRET_KEY": "ambient-example",
             "DOCKER_HOST": "unix:///fixture.sock", "DOCKER_CONTEXT": "", **environment},
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
    state.mkdir(mode=0o700)
    keys = ("MYSQL_PASSWORD", "MYSQL_ROOT_PASSWORD", "SECRET_KEY", "INIT_ADMIN_PASSWORD")
    config = {key: "a" * 48 for key in keys}
    (state / "panellab-local.json").write_text(json.dumps({**config, "project": "production"}))
    (state / "panellab-local.json").chmod(0o600)
    docker = tmp_path / "docker"
    docker.write_text("#!/bin/sh\necho 'docker invoked' >&2\nexit 0\n")
    docker.chmod(0o700)
    result = launch(tmp_path, "stop")
    assert result.returncode != 0
    assert "实例配置无效" in result.stderr
    assert "docker invoked" not in result.stderr


def docker_recording_endpoint(tmp_path):
    docker = tmp_path / "docker"
    docker.write_text(
        '#!/bin/sh\n'
        'if [ "$1" = context ]; then echo "$FIXTURE_CONTEXT_HOST"; exit 0; fi\n'
        'printf "%s|%s|%s\\n" "$1 $2" "$DOCKER_HOST" "$DOCKER_CONTEXT" >> "$CALLS"\n'
    )
    docker.chmod(0o700)
    return tmp_path / "calls"


@pytest.mark.parametrize(("host", "context"), [
    ("tcp://fixture.invalid:2375", ""), ("", "remote"), ("", ""), ("unix:///fixture.sock", "remote"),
])
def test_refuses_remote_docker_before_seeding_or_building(tmp_path, host, context):
    calls = docker_recording_endpoint(tmp_path)
    result = launch(tmp_path, DOCKER_HOST=host, DOCKER_CONTEXT=context,
                    FIXTURE_CONTEXT_HOST="ssh://fixture.invalid", CALLS=str(calls))
    assert result.returncode != 0
    assert "本机 Docker" in result.stderr
    assert not (tmp_path / "state").exists()
    assert not calls.exists()


def test_build_and_compose_are_pinned_to_inspected_socket(tmp_path):
    calls = docker_recording_endpoint(tmp_path)
    local = "unix:///colima/default/docker.sock"
    result = launch(tmp_path, DOCKER_HOST="", DOCKER_CONTEXT="colima",
                    FIXTURE_CONTEXT_HOST=local, CALLS=str(calls))
    assert result.returncode == 0, result.stderr
    commands = [line.split("|") for line in calls.read_text().splitlines()]
    assert any(command.startswith("build ") for command, _, _ in commands)
    assert any(command.startswith("compose --project-name") for command, _, _ in commands)
    assert all(host == local and context == "" for _, host, context in commands)


def valid_state(directory):
    config = {key: "private-" + "b" * 40 for key in
              ("MYSQL_PASSWORD", "MYSQL_ROOT_PASSWORD", "SECRET_KEY", "INIT_ADMIN_PASSWORD")}
    config["project"] = "panellab-local-0123456789abcdef"
    file = directory / "panellab-local.json"
    file.write_text(json.dumps(config))
    file.chmod(0o600)
    return file


@pytest.mark.parametrize("case", [
    "writable-directory", "replaceable-parent", "symlink-parent", "symlink-directory",
    "symlink-file", "dangling-file", "public-file", "fifo",
])
def test_unsafe_state_is_refused_without_password_or_modification(tmp_path, case):
    parent = tmp_path / "parent"
    parent.mkdir(mode=0o700)
    state = parent / "state"
    state.mkdir(mode=0o700)
    file = valid_state(state)
    if case == "writable-directory":
        state.chmod(0o777)
    elif case == "replaceable-parent":
        parent.chmod(0o777)
    elif case == "symlink-parent":
        link = tmp_path / "linked"
        link.symlink_to(parent)
        state = link / "state"
    elif case == "symlink-directory":
        link = tmp_path / "linked"
        link.symlink_to(state)
        state = link
    elif case == "symlink-file":
        file.rename(state / "existing")
        file.symlink_to(state / "existing")
    elif case == "dangling-file":
        file.unlink()
        file.symlink_to(state / "missing")
    elif case == "public-file":
        file.chmod(0o644)
    elif case == "fifo":
        file.unlink()
        os.mkfifo(file, mode=0o600)
    result = launch(tmp_path, "password", state_dir=state)
    assert result.returncode != 0 and not result.stdout
    assert "不安全" in result.stderr
    if case == "dangling-file":
        result = launch(tmp_path, state_dir=state)
        assert result.returncode != 0 and file.is_symlink()
        assert not (state / "missing").exists()


def test_public_sticky_ancestor_protects_private_instance(tmp_path):
    parent = tmp_path / "temp"
    parent.mkdir(mode=0o700)
    parent.chmod(0o1777)
    state = parent / "state"
    state.mkdir(mode=0o700)
    valid_state(state)
    assert launch(tmp_path, "password", state_dir=state).returncode == 0

@pytest.mark.skipif(sys.platform != "darwin", reason="macOS system /tmp alias")
def test_macos_tmp_alias_allows_a_private_state_directory(tmp_path):
    with tempfile.TemporaryDirectory(dir="/tmp") as private:
        state = Path(private)
        valid_state(state)
        assert launch(tmp_path, "password", state_dir=state).returncode == 0
