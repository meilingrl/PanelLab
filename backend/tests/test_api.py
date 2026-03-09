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


def test_sites_unauthorized(client):
    """站点所有接口未登录时返回 401。"""
    assert client.get("/api/sites").status_code == 401
    assert client.post("/api/sites", json={}).status_code == 401
    assert client.get("/api/sites/1").status_code == 401
    assert client.put("/api/sites/1", json={}).status_code == 401
    assert client.delete("/api/sites/1").status_code == 401
    assert client.post("/api/sites/1/apply").status_code == 401


def test_sites_not_found(client):
    """访问不存在的站点返回 404。"""
    login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    token = login.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    assert client.get("/api/sites/99999", headers=headers).status_code == 404
    assert client.put(
        "/api/sites/99999",
        headers=headers,
        json={
            "name": "ghost", "domain": "ghost.example.com",
            "site_type": "proxy", "root_path": "",
            "proxy_target": "http://127.0.0.1:4000",
            "listen_port": 80, "enabled": True,
        },
    ).status_code == 404
    assert client.delete("/api/sites/99999", headers=headers).status_code == 404
    assert client.post("/api/sites/99999/apply", headers=headers).status_code == 404


def test_sites_duplicate(client):
    """重复名称或域名返回 409。"""
    login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    token = login.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    base = {
        "name": "dup-site", "domain": "dup.example.com",
        "site_type": "proxy", "root_path": "",
        "proxy_target": "http://127.0.0.1:5000",
        "listen_port": 80, "enabled": True,
    }
    r = client.post("/api/sites", headers=headers, json=base)
    assert r.status_code == 200
    site_id = r.json()["id"]

    # 重复名称（不同域名）
    r2 = client.post("/api/sites", headers=headers, json={**base, "domain": "other-dup.example.com"})
    assert r2.status_code == 409

    # 重复域名（不同名称）
    r3 = client.post("/api/sites", headers=headers, json={**base, "name": "other-dup-site"})
    assert r3.status_code == 409

    client.delete(f"/api/sites/{site_id}", headers=headers)


def test_sites_validation(client):
    """非法请求参数返回 422。"""
    login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    token = login.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 静态站点缺少 root_path
    assert client.post("/api/sites", headers=headers, json={
        "name": "bad-static", "domain": "bad-static.example.com",
        "site_type": "static", "root_path": "", "proxy_target": "",
        "listen_port": 80, "enabled": True,
    }).status_code == 422

    # 代理站点缺少 proxy_target
    assert client.post("/api/sites", headers=headers, json={
        "name": "bad-proxy", "domain": "bad-proxy.example.com",
        "site_type": "proxy", "root_path": "", "proxy_target": "",
        "listen_port": 80, "enabled": True,
    }).status_code == 422

    # 端口超出范围
    assert client.post("/api/sites", headers=headers, json={
        "name": "bad-port", "domain": "bad-port.example.com",
        "site_type": "proxy", "root_path": "", "proxy_target": "http://127.0.0.1:3000",
        "listen_port": 0, "enabled": True,
    }).status_code == 422

    # 名称为空
    assert client.post("/api/sites", headers=headers, json={
        "name": "", "domain": "bad-name.example.com",
        "site_type": "proxy", "root_path": "", "proxy_target": "http://127.0.0.1:3000",
        "listen_port": 80, "enabled": True,
    }).status_code == 422


def test_sites_crud_and_apply(client):
    login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert login.status_code == 200
    token = login.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    create = client.post(
        "/api/sites",
        headers=headers,
        json={
            "name": "blog",
            "domain": "blog.example.com",
            "site_type": "proxy",
            "root_path": "",
            "proxy_target": "http://127.0.0.1:3000",
            "listen_port": 80,
            "enabled": True,
        },
    )
    assert create.status_code == 200
    site = create.json()
    assert site["name"] == "blog"
    assert site["site_type"] == "proxy"
    site_id = site["id"]

    list_resp = client.get("/api/sites", headers=headers)
    assert list_resp.status_code == 200
    assert any(item["id"] == site_id for item in list_resp.json()["items"])

    update = client.put(
        f"/api/sites/{site_id}",
        headers=headers,
        json={
            "name": "blog-static",
            "domain": "static.example.com",
            "site_type": "static",
            "root_path": "/var/www/blog",
            "proxy_target": "",
            "listen_port": 8080,
            "enabled": True,
        },
    )
    assert update.status_code == 200
    updated = update.json()
    assert updated["name"] == "blog-static"
    assert updated["site_type"] == "static"
    assert updated["listen_port"] == 8080

    apply_resp = client.post(f"/api/sites/{site_id}/apply", headers=headers)
    assert apply_resp.status_code == 200
    assert apply_resp.json()["id"] == site_id

    delete_resp = client.delete(f"/api/sites/{site_id}", headers=headers)
    assert delete_resp.status_code == 200


# ---------- 阶段 4：数据库管理 ----------
def test_db_unauthorized(client):
    """数据库管理接口未登录时返回 401。"""
    assert client.get("/api/db/databases").status_code == 401
    assert client.get("/api/db/users").status_code == 401
    assert client.get("/api/db/jobs").status_code == 401
    assert client.post("/api/db/databases", json={"name": "x", "charset": "utf8mb4", "collation": "utf8mb4_unicode_ci"}).status_code == 401
    assert client.post("/api/db/users", json={"username": "u", "password": "pass1234", "host": "%", "database": "d", "privileges": ["SELECT"]}).status_code == 401
    assert client.delete("/api/db/databases/testdb").status_code == 401
    assert client.delete("/api/db/users/testuser").status_code == 401


def test_db_jobs_stub(client):
    """计划任务预留：GET /api/db/jobs 需登录，返回空列表；POST/DELETE 返回 501。"""
    login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert login.status_code == 200
    token = login.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    r = client.get("/api/db/jobs", headers=headers)
    assert r.status_code == 200
    assert r.json() == {"items": []}
    assert client.post("/api/db/jobs", headers=headers).status_code == 501
    assert client.delete("/api/db/jobs/1", headers=headers).status_code == 501
