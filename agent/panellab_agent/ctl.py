"""panellab-agentctl: forward one JSON request line to the agent socket.

    echo '{"protocol_version":1,"op":"status","args":{}}' | panellab-agentctl
    panellab-agentctl drain [--force]
    panellab-agentctl undrain

`drain` re-sends the drain op on every poll, which renews the agent's 30 s
drain lease; if this client dies the agent resumes scheduling by itself.

Exit codes: 0 response written to stdout; 1 drain/undrain failed or timed out;
2 bad usage or bad JSON on stdin; 3 agent unreachable; 4 permission denied.
Errors on stderr are one JSON object {"code": ..., "message": ...}.
"""
from __future__ import annotations

import argparse
import errno
import json
import socket
import sys
import time
from typing import Any, Dict, List, Optional

from . import PROTOCOL_VERSION

DEFAULT_SOCKET = "/run/panellab-agent/agent.sock"
POLL_SECONDS = 1.0
TIMEOUT_SECONDS = 120
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_UNREACHABLE = 3
EXIT_PERMISSION = 4


class _Fail(Exception):
    def __init__(self, exit_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.code = code
        self.message = message


def _unreachable(message: str = "无法连接 PanelLab Agent") -> _Fail:
    return _Fail(EXIT_UNREACHABLE, "agent_unreachable", message)


def _connect(path: str) -> socket.socket:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(TIMEOUT_SECONDS)  # a timeout surfaces as agent_unreachable (exit 3)
    try:
        sock.connect(path)
    except BaseException:
        sock.close()
        raise
    return sock


def _exchange(path: str, line: bytes) -> bytes:
    try:
        sock = _connect(path)
    except OSError as exc:
        if isinstance(exc, PermissionError) or exc.errno in (errno.EACCES, errno.EPERM):
            raise _Fail(EXIT_PERMISSION, "agent_permission", "没有权限访问 PanelLab Agent")
        raise _unreachable()
    try:
        sock.sendall(line)
        data = b""
        while not data.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
    except OSError:
        raise _unreachable("与 PanelLab Agent 的连接中断")
    finally:
        sock.close()
    if not data.endswith(b"\n"):
        raise _unreachable("PanelLab Agent 没有返回完整响应")
    return data


def _call(path: str, op: str, args: Dict[str, Any]) -> Dict[str, Any]:
    req = {"protocol_version": PROTOCOL_VERSION, "op": op, "args": args}
    data = _exchange(path, (json.dumps(req) + "\n").encode("utf-8"))
    try:
        resp = json.loads(data.decode("utf-8"))
    except ValueError:
        raise _unreachable("PanelLab Agent 返回了无法解析的响应")
    if not isinstance(resp, dict):
        raise _unreachable("PanelLab Agent 返回了无法解析的响应")
    return resp


def _say(obj: Dict[str, Any], stream) -> None:
    stream.write(json.dumps(obj, ensure_ascii=False) + "\n")
    stream.flush()


def _forward(path: str) -> int:
    line = sys.stdin.readline()
    try:
        json.loads(line)
    except ValueError:
        raise _Fail(EXIT_USAGE, "bad_request", "stdin 第一行不是有效的 JSON")
    data = _exchange(path, line.rstrip("\r\n").encode("utf-8") + b"\n")
    sys.stdout.write(data.decode("utf-8", errors="replace"))
    sys.stdout.flush()
    return 0


def _drain(path: str, force: bool) -> int:
    resp = _call(path, "drain", {"force": force})
    deadline = None
    last = None
    while True:
        if not resp.get("ok"):
            _say(resp, sys.stdout)
            return EXIT_FAILED
        result = resp["result"]
        if deadline is None:
            deadline = time.monotonic() + float(result.get("wait_seconds", 60))
        active = int(result.get("active_count", 0))
        if active == 0:
            sys.stderr.write("排空完成：没有正在运行的作业\n")
            _say(resp, sys.stdout)
            return 0
        if active != last:
            sys.stderr.write("排空中：还有 %d 个运行未结束\n" % active)
            sys.stderr.flush()
            last = active
        if time.monotonic() >= deadline:
            raise _Fail(EXIT_FAILED, "drain_timeout", "等待运行结束超时")
        time.sleep(POLL_SECONDS)
        resp = _call(path, "drain", {})


def _undrain(path: str) -> int:
    resp = _call(path, "undrain", {})
    _say(resp, sys.stdout)
    if not resp.get("ok"):
        return EXIT_FAILED
    sys.stderr.write("已退出排空状态，恢复调度\n")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="panellab-agentctl",
                                     description="Send one JSON request to the PanelLab Agent.")
    parser.add_argument("--socket", default=DEFAULT_SOCKET)
    sub = parser.add_subparsers(dest="command")
    drain = sub.add_parser("drain", help="stop starting runs and wait for running ones")
    drain.add_argument("--force", action="store_true", help="cancel running runs")
    sub.add_parser("undrain", help="leave the drain state and resume scheduling")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_USAGE if exc.code else 0
    try:
        if args.command == "drain":
            return _drain(args.socket, args.force)
        if args.command == "undrain":
            return _undrain(args.socket)
        return _forward(args.socket)
    except _Fail as fail:
        _say({"code": fail.code, "message": fail.message}, sys.stderr)
        return fail.exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
