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


# ---------- 多途径登录：手机验证码（mock）----------

def test_sms_send_bad_phone(client):
    r = client.post("/api/auth/sms/send", json={"phone": "123"})
    assert r.status_code == 422  # validation error
    r2 = client.post("/api/auth/sms/send", json={"phone": "1380013800"})
    assert r2.status_code == 422


def test_sms_send_ok(client):
    r = client.post("/api/auth/sms/send", json={"phone": "13800138000"})
    assert r.status_code == 200
    assert "message" in r.json()


def test_sms_send_throttle(client):
    client.post("/api/auth/sms/send", json={"phone": "13900139000"})
    r = client.post("/api/auth/sms/send", json={"phone": "13900139000"})
    assert r.status_code == 429


def test_sms_login_no_code(client):
    r = client.post("/api/auth/sms/login", json={"phone": "13800138000", "code": "000000"})
    assert r.status_code == 401


def test_sms_login_ok(client):
    client.post("/api/auth/sms/send", json={"phone": "13700137000"})
    r = client.post("/api/auth/sms/login", json={"phone": "13700137000", "code": "123456"})
    assert r.status_code == 200
    data = r.json()
    assert "token" in data
    assert data.get("user", {}).get("username", "").startswith("phone_")


def test_sms_login_wrong_code(client):
    client.post("/api/auth/sms/send", json={"phone": "13600136000"})
    r = client.post("/api/auth/sms/login", json={"phone": "13600136000", "code": "999999"})
    assert r.status_code == 401


# ---------- 多途径登录：微信/QQ mock ----------

def test_wechat_callback_mock(client):
    r = client.post("/api/auth/wechat/callback")
    assert r.status_code == 200
    data = r.json()
    assert "token" in data
    assert data.get("user", {}).get("username", "").startswith("wx_")


def test_qq_callback_mock(client):
    r = client.post("/api/auth/qq/callback")
    assert r.status_code == 200
    data = r.json()
    assert "token" in data
    assert data.get("user", {}).get("username", "").startswith("qq_")


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


def _site_headers(client):
    token = client.post("/api/auth/login", json={"username": "admin", "password": "admin"}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _site_change(client, headers, operation, config=None, site_id=None):
    body = {"operation": operation}
    if config is not None: body["config"] = config
    if site_id is not None: body["site_id"] = site_id
    preview = client.post("/api/site-changes/preview", headers=headers, json=body)
    if preview.status_code != 200: return preview
    return client.post("/api/site-changes/execute", headers=headers, json={"token": preview.json()["token"]})


def test_sites_not_found(client):
    headers = _site_headers(client)
    assert client.get("/api/sites/99999", headers=headers).status_code == 404
    for operation in ("apply", "delete"):
        assert _site_change(client, headers, operation, site_id=99999).status_code == 404


def test_sites_duplicate(client):
    headers = _site_headers(client)
    base = {"name": "dup", "domain": "dup.example.com", "site_type": "proxy", "proxy_target": "http://127.0.0.1:5000"}
    assert _site_change(client, headers, "create", base).status_code == 200
    assert _site_change(client, headers, "create", base | {"domain": "other.example.com"}).status_code == 409
    assert _site_change(client, headers, "create", base | {"name": "other"}).status_code == 409


@pytest.mark.parametrize("patch", [{"root_path": ""}, {"site_type": "proxy"}, {"listen_port": 0}, {"name": ""}])
def test_sites_validation(client, patch):
    base = {"name": "web", "domain": "web.example.com", "site_type": "static", "root_path": "/var/www/web"}
    assert _site_change(client, _site_headers(client), "create", base | patch).status_code == 422


def test_sites_crud_and_apply(client):
    headers = _site_headers(client)
    base = {"name": "blog", "domain": "blog.example.com", "site_type": "proxy", "proxy_target": "http://127.0.0.1:3000"}
    created = _site_change(client, headers, "create", base)
    assert created.status_code == 200
    site = created.json()["site"]
    assert site["name"] == "blog" and site["site_type"] == "proxy"
    assert any(item["id"] == site["id"] for item in client.get("/api/sites", headers=headers).json()["items"])
    body = {"name": "blog-static", "domain": "static.example.com", "site_type": "static", "root_path": "/var/www/blog", "listen_port": 8080}
    updated = _site_change(client, headers, "update", body, site["id"])
    assert updated.status_code == 200
    assert updated.json()["site"]["name"] == "blog-static"
    assert updated.json()["site"]["site_type"] == "static"
    assert updated.json()["site"]["listen_port"] == 8080
    applied = _site_change(client, headers, "apply", site_id=site["id"])
    assert applied.status_code == 200 and applied.json()["site"]["id"] == site["id"]
    assert _site_change(client, headers, "delete", site_id=site["id"]).status_code == 200
    assert client.get(f"/api/sites/{site['id']}", headers=headers).status_code == 404


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
