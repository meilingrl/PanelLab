"""Start the isolated local PanelLab stack with Python 3 and Docker Compose."""
import argparse
import fcntl
import json
import os
import re
from pathlib import Path
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SECRET_KEYS = ("MYSQL_PASSWORD", "MYSQL_ROOT_PASSWORD", "SECRET_KEY", "INIT_ADMIN_PASSWORD")


def private_directory(path, create):
    path = Path(os.path.abspath(path.expanduser()))
    # macOS exposes /tmp and /var as root-owned aliases for /private/tmp and /private/var.
    if sys.platform == "darwin" and len(path.parts) >= 2 and path.parts[1] in ("tmp", "var"):
        alias = Path("/") / path.parts[1]
        if alias.is_symlink() and alias.lstat().st_uid == 0 and os.readlink(alias) == "private/" + alias.name:
            path = Path("/private").joinpath(*path.parts[1:])

    uid = os.getuid()
    for parent in (*reversed(path.parents), path):
        try:
            info = parent.lstat()
        except FileNotFoundError:
            if not create:
                raise ValueError("此目录尚未初始化，请先启动面板。") from None
            parent.mkdir(mode=0o700, exist_ok=True)
            info = parent.lstat()
        # In a trusted sticky directory, another user cannot rename our child.
        replaceable = info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX
        if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (uid, 0) or replaceable:
            raise ValueError(f"不安全的实例目录：{parent}")
    if info.st_uid != uid or info.st_mode & 0o077:
        raise ValueError(f"不安全的实例目录，请使用当前用户独占的目录：{path}")
    return path


def load_instance(directory, create):
    path = directory / "panellab-local.json"
    try:
        info = path.lstat()
    except FileNotFoundError:
        if not create:
            raise ValueError("此目录尚未初始化，请先启动面板。") from None
        instance = {key: secrets.token_hex(24) for key in SECRET_KEYS}
        instance["project"] = "panellab-local-" + secrets.token_hex(8)
        with tempfile.NamedTemporaryFile(mode="w", dir=directory, prefix=path.name) as temp:
            json.dump(instance, temp)
            temp.flush()
            os.fsync(temp.fileno())
            os.link(temp.name, path)
        return instance
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError(f"不安全的实例凭据，请使用当前用户独占的常规文件：{path}")
    with path.open() as saved:
        instance = json.load(saved)
    if not isinstance(instance, dict) or not re.fullmatch(r"panellab-local-[0-9a-f]{16}", str(instance.get("project"))):
        raise ValueError(f"实例配置无效，请核对私有配置文件：{path}")
    if any(not isinstance(instance.get(key), str) or not instance[key] for key in SECRET_KEYS):
        raise ValueError(f"实例配置缺少凭据，请从备份恢复：{path}")
    return instance


def local_docker_env():
    env = os.environ.copy()
    host = env.pop("DOCKER_HOST", "")
    context = env.pop("DOCKER_CONTEXT", "")
    validate = r"unix:///[^?#\s]+"
    if host and not re.fullmatch(validate, host):
        raise ValueError("本机 Docker 启动器仅支持 Unix 套接字，请检查 DOCKER_HOST。")
    if context or not host:
        # context inspect reads local CLI configuration without contacting the daemon.
        command = ["docker", "context", "inspect", "--format", '{{(index .Endpoints "docker").Host}}']
        if context:
            command += ["--", context]
        selected = subprocess.run(command, env=env, capture_output=True, text=True, check=True)
        host = selected.stdout.strip()
    if not re.fullmatch(validate, host):
        raise ValueError("请选择使用 Unix 套接字的本机 Docker context。")
    env["DOCKER_HOST"] = host
    for key in ("DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "BUILDX_BUILDER", "BUILDKIT_HOST"):
        env.pop(key, None)
    return env


def compose_cli(env):
    if subprocess.run(["docker", "compose", "version"], env=env, capture_output=True).returncode == 0:
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
    docker_env = local_docker_env() if args.action != "password" else None
    start = args.action == "start"
    directory = private_directory(args.state_dir, start)

    # Serialize initialization, builds and container replacement for this instance.
    fd = os.open(directory, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        instance = load_instance(directory, start)
        if args.action == "password":
            print(instance["INIT_ADMIN_PASSWORD"])
            return
        compose = compose_cli(docker_env)
        project = instance["project"]
        compose += ["--project-name", project, "--env-file", os.devnull, "-f", str(ROOT / "compose.local.yaml")]
        env = {**docker_env, **{key: instance[key] for key in SECRET_KEYS}, "PANEL_LOCAL_PORT": str(args.port)}
        if start:
            subprocess.run(["docker", "build", "-t", project + "-app:local", str(ROOT)], env=docker_env, check=True)
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
