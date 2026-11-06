"""Start the isolated local PanelLab stack with Python 3 and Docker Compose."""
import argparse
import fcntl
import json
import os
import re
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SECRET_KEYS = ("MYSQL_PASSWORD", "MYSQL_ROOT_PASSWORD", "SECRET_KEY", "INIT_ADMIN_PASSWORD")


def load_instance(directory, create):
    path = directory / "panellab-local.json"
    if not path.exists():
        if not create:
            raise ValueError("此目录尚未初始化，请先启动面板。")
        instance = {key: secrets.token_hex(24) for key in SECRET_KEYS}
        instance["project"] = "panellab-local-" + secrets.token_hex(8)
        with tempfile.NamedTemporaryFile(mode="w", dir=directory, prefix=path.name, delete=False) as temp:
            json.dump(instance, temp)
        os.replace(temp.name, path)
    with path.open() as saved:
        instance = json.load(saved)
    if not isinstance(instance, dict) or not re.fullmatch(r"panellab-local-[0-9a-f]{16}", str(instance.get("project"))):
        raise ValueError(f"实例配置无效，请核对私有配置文件：{path}")
    if any(not isinstance(instance.get(key), str) or not instance[key] for key in SECRET_KEYS):
        raise ValueError(f"实例配置缺少凭据，请从备份恢复：{path}")
    return instance


def compose_cli():
    if subprocess.run(["docker", "compose", "version"], capture_output=True).returncode == 0:
        return ["docker", "compose"]
    if shutil.which("docker-compose"):
        return ["docker-compose"]
    raise ValueError("请安装支持 up --wait 的 Docker Compose。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", choices=("start", "stop", "password", "logs"), default="start")
    parser.add_argument("--state-dir", type=Path, default=ROOT / "data" / "local-panel")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port 取值范围为 1–65535")
    directory = args.state_dir.expanduser().resolve()
    start = args.action == "start"
    if start:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    elif not directory.is_dir():
        raise ValueError("此目录尚未初始化，请先启动面板。")

    # Serialize initialization, builds and container replacement for this instance.
    fd = os.open(directory, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        instance = load_instance(directory, start)
        if args.action == "password":
            print(instance["INIT_ADMIN_PASSWORD"])
            return
        compose = compose_cli()
        project = instance["project"]
        compose += ["--project-name", project, "--env-file", os.devnull, "-f", str(ROOT / "compose.local.yaml")]
        env = {**os.environ, **{key: instance[key] for key in SECRET_KEYS}, "PANEL_LOCAL_PORT": str(args.port)}
        if start:
            subprocess.run(["docker", "build", "-t", project + "-app:local", str(ROOT)], check=True)
            subprocess.run(compose + ["up", "--wait", "--wait-timeout", "180"], env=env, check=True)
            print(f"PanelLab 已就绪：http://127.0.0.1:{args.port}  用户名：admin")
            print("查看初始密码：将 password 加到启动命令中。")
        else:
            command = ["down"] if args.action == "stop" else ["logs", "--tail", "80"]
            subprocess.run(compose + command, env=env, check=True)
    finally:
        os.close(fd)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        sys.exit(exc.returncode)
    except (ValueError, KeyError, OSError) as exc:
        sys.exit(str(exc))
