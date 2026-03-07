#!/usr/bin/env python3
"""
PanelLab 统一验收脚本（接口冒烟）。
Python 3.9+，仅用标准库。
要求：后端已启动，且已执行过 init_db（存在 admin 用户）。
用法：在项目根目录执行  python scripts/smoke.py  [BASE_URL]
默认 BASE_URL=http://127.0.0.1:8000
"""
import json
import os
import sys
import urllib.error
import urllib.request


def main():
    base = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("PANELLAB_BASE_URL", "http://127.0.0.1:8000")).rstrip("/")
    username = os.environ.get("PANELLAB_SMOKE_USER", "admin")
    password = os.environ.get("PANELLAB_SMOKE_PASSWORD", "admin")

    def get(path: str, token=None):
        req = urllib.request.Request(f"{base}{path}")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            try:
                data = json.loads(body)
            except Exception:
                data = {"detail": body}
            return e.code, data
        except Exception as e:
            print(f"请求失败: {e}")
            sys.exit(2)

    def post(path: str, body: dict, token=None):
        data = json.dumps(body).encode()
        req = urllib.request.Request(f"{base}{path}", data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            try:
                out = json.loads(body)
            except Exception:
                out = {"detail": body}
            return e.code, out
        except Exception as e:
            print(f"请求失败: {e}")
            sys.exit(2)

    print(f"验收目标: {base}")
    # 1. 健康检查
    code, data = get("/api/health")
    if code != 200 or data.get("status") != "ok":
        print(f"FAIL 健康检查: status={code} body={data}")
        sys.exit(1)
    print("OK 健康检查")

    # 2. 登录
    code, data = post("/api/auth/login", {"username": username, "password": password})
    if code != 200 or "token" not in data:
        print(f"FAIL 登录: status={code} body={data}")
        sys.exit(1)
    token = data["token"]
    print("OK 登录")

    # 3. 监控接口（需登录）
    code, data = get("/api/monitor/stats?target=local", token=token)
    if code != 200:
        print(f"FAIL 监控 stats: status={code} body={data}")
        sys.exit(1)
    if "cpu_percent" not in data and "memory" not in data:
        print(f"FAIL 监控 stats: 响应缺少预期字段 body={data}")
        sys.exit(1)
    print("OK 监控 stats")

    print("验收通过")
    sys.exit(0)


if __name__ == "__main__":
    main()
