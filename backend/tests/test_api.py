"""接口冒烟测试：健康检查、登录、监控（需登录）。"""
import pytest


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_login_ok(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert r.status_code == 200
    data = r.json()
    assert "token" in data
    assert data.get("user", {}).get("username") == "admin"


def test_login_bad_password(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert r.status_code == 401


def test_monitor_stats_unauthorized(client):
    r = client.get("/api/monitor/stats?target=local")
    assert r.status_code == 401


def test_monitor_stats_ok(client):
    login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert login.status_code == 200
    token = login.json()["token"]
    r = client.get("/api/monitor/stats?target=local", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    data = r.json()
    assert "cpu_percent" in data or "memory" in data or "disk" in data or "network" in data
