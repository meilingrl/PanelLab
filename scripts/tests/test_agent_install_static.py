"""Static checks for the agent's install script and systemd unit.

The real install runs in scripts/verify_agent_lab.py (systemd in Docker);
this only guards syntax and the unit lines the design depends on.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALL = ROOT / "agent" / "install.sh"
UNIT = ROOT / "agent" / "systemd" / "panellab-agent.service"

REQUIRED_UNIT_LINES = (
    "User=panellab",
    "Group=panellab",
    "ExecStart=/usr/bin/python3 -m panellab_agent.daemon --config /etc/panellab-agent/agent.json"
    " --data-dir /var/lib/panellab-agent --socket /run/panellab-agent/agent.sock",
    "Environment=PYTHONPATH=/opt/panellab-agent/current",
    "RuntimeDirectory=panellab-agent",
    "RuntimeDirectoryMode=0750",
    "Restart=always",
    "RestartSec=5",
    "NoNewPrivileges=yes",
    "ProtectSystem=full",
    "PrivateTmp=yes",
    "ReadWritePaths=/etc/panellab-agent",
    "KillMode=mixed",
    "TimeoutStopSec=150",
    "UMask=0007",
)


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_install_script_parses():
    result = subprocess.run(["bash", "-n", str(INSTALL)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("line", REQUIRED_UNIT_LINES)
def test_unit_contains_required_line(line):
    lines = [l.strip() for l in UNIT.read_text(encoding="utf-8").splitlines()]
    assert line in lines


def test_install_script_never_modifies_agent_account():
    text = INSTALL.read_text(encoding="utf-8")
    for cmd in ("userdel", "usermod"):
        for m in re.finditer(r"\b%s\b[^\n]*" % cmd, text):
            assert not re.search(r"(^|\s)['\"]?agent['\"]?(\s|$)", m.group(0)), m.group(0)


def test_install_script_supports_uninstall_and_purge():
    text = INSTALL.read_text(encoding="utf-8")
    assert "--uninstall" in text
    assert "--purge" in text


def test_install_script_is_strict_and_writes_ctl_wrapper():
    text = INSTALL.read_text(encoding="utf-8")
    assert re.search(r"^set -eu", text, re.M)
    assert "/usr/local/bin/panellab-agentctl" in text
    assert "PYTHONPATH=/opt/panellab-agent/current" in text


def test_ctl_wrapper_leaves_the_callers_directory_before_exec():
    text = INSTALL.read_text(encoding="utf-8")
    wrapper = text.split("<<'WRAPPER'", 1)[1].split("\nWRAPPER\n", 1)[0]
    lines = [l.strip() for l in wrapper.strip().splitlines()]
    assert lines[0] == "#!/bin/sh"
    assert "cd / || exit 1" in lines
    exec_at = next(i for i, l in enumerate(lines) if l.startswith("exec "))
    assert lines.index("cd / || exit 1") < exec_at


def test_unit_does_not_run_from_the_data_dir():
    assert "WorkingDirectory" not in UNIT.read_text(encoding="utf-8")


def test_install_script_runs_from_root_dir():
    text = INSTALL.read_text(encoding="utf-8")
    assert re.search(r"^cd /$", text, re.M)
