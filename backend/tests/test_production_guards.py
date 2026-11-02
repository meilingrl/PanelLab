"""Production entry points are closed unless explicitly authorised."""
import bcrypt
import pytest
from pydantic import ValidationError
from config import Settings
from database import SessionLocal
from models.user import User
from routers.auth import _password_placeholder, _verify_password

SAFE = dict(app_env="production", secret_key="a-unique-secret-key-at-least-32-characters", init_admin_password="strong-initial-password", nginx_skip_apply=False, enable_demo_auth=False)

@pytest.mark.parametrize("change", [
    {"app_env": "prod"}, {"secret_key": "short"}, {"secret_key": "change-me-in-production"},
    {"secret_key": "your-secret-key-change-in-production"},
    {"init_admin_password": "short"}, {"init_admin_password": "admin"},
    {"nginx_skip_apply": True}, {"enable_demo_auth": True},
])
def test_invalid_production_configuration_is_rejected(change):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **(SAFE | change))

@pytest.fixture
def production(monkeypatch):
    for key, value in SAFE.items():
        monkeypatch.setenv(key.upper(), str(value).lower() if isinstance(value, bool) else value)
    monkeypatch.setenv("OPERATOR_USERNAMES", "admin, operator")

@pytest.mark.parametrize("method,path,body", [
    ("post", "/api/auth/register", {"username": "someone", "password": "password123"}),
    ("post", "/api/auth/sms/send", {"phone": "13800138000"}),
    ("post", "/api/auth/sms/login", {"phone": "13800138000", "code": "123456"}),
    ("get", "/api/auth/wechat/qr", None),
    ("get", "/api/auth/wechat/callback?code=x&state=x", None),
    ("get", "/api/auth/wechat/poll?state=x", None),
    ("post", "/api/auth/wechat/callback", None),
    ("get", "/api/auth/qq/url", None),
    ("get", "/api/auth/qq/callback?code=x&state=x", None),
    ("post", "/api/auth/qq/callback", None),
])
def test_production_closed_auth_routes_never_issue_tokens(client, production, method, path, body):
    response = client.request(method, path, json=body, follow_redirects=False)
    assert response.status_code in (403, 503)
    assert "token" not in response.text.lower()
    assert "token" not in response.headers.get("location", "").lower()

def test_capabilities_describe_production_and_development(client, production, monkeypatch):
    assert client.get('/api/auth/capabilities').json() == dict(password=True, register=False, sms=False, wechat=False, qq=False, demo=False)
    monkeypatch.setenv('APP_ENV', 'test')
    assert client.get('/api/auth/capabilities').json() == dict(password=True, register=True, sms=False, wechat=False, qq=False, demo=False)
    monkeypatch.setenv('ENABLE_DEMO_AUTH', 'true')
    assert client.get('/api/auth/capabilities').json() == dict(password=True, register=True, sms=True, wechat=True, qq=True, demo=True)

@pytest.mark.parametrize('path', ['/api/sites', '/api/sites/1', '/api/db/databases', '/api/db/users', '/api/db/jobs'])
def test_ordinary_user_cannot_read_shared_resources(client, production, path):
    db = SessionLocal()
    if not db.query(User).filter_by(username='ordinary').first():
        db.add(User(username='ordinary', password_hash=bcrypt.hashpw(b'password123', bcrypt.gensalt()).decode()))
        db.commit()
    db.close()
    token = client.post('/api/auth/login', json={'username': 'ordinary', 'password': 'password123'}).json()['token']
    assert client.get(path, headers={'Authorization': f'Bearer {token}'}).status_code == 403

def test_operator_can_read_sites_and_jobs(client, production):
    token = client.post('/api/auth/login', json={'username': 'admin', 'password': 'admin'}).json()['token']
    headers = {'Authorization': f'Bearer {token}'}
    assert client.get('/api/sites', headers=headers).status_code == 200
    assert client.get('/api/db/jobs', headers=headers).status_code == 200

def test_placeholder_password_is_not_publicly_guessable():
    assert not _verify_password('no-password-placeholder', _password_placeholder())


@pytest.mark.parametrize("method,path,body", [
    ("post", "/api/sites", {"name": "x", "domain": "x.test", "site_type": "static", "root_path": "/tmp", "listen_port": 80, "enabled": False}),
    ("delete", "/api/sites/1", None),
    ("post", "/api/db/jobs", None),
    ("delete", "/api/db/databases/example", None),
])
def test_ordinary_user_cannot_write_shared_resources(client, production, method, path, body):
    db = SessionLocal()
    if not db.query(User).filter_by(username="ordinary").first():
        db.add(User(username="ordinary", password_hash=bcrypt.hashpw(b"password123", bcrypt.gensalt()).decode()))
        db.commit()
    db.close()
    token = client.post("/api/auth/login", json={"username": "ordinary", "password": "password123"}).json()["token"]
    assert client.request(method, path, json=body, headers={"Authorization": f"Bearer {token}"}).status_code == 403


def test_development_demo_routes_closed_by_default(client, monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("ENABLE_DEMO_AUTH", "false")
    for method, path in [("post", "/api/auth/sms/send"), ("get", "/api/auth/wechat/callback?code=x"), ("get", "/api/auth/wechat/poll?state=x"), ("post", "/api/auth/wechat/callback"), ("get", "/api/auth/qq/callback?code=x"), ("post", "/api/auth/qq/callback")]:
        response = client.request(method, path, json={"phone": "13800138000"}, follow_redirects=False)
        assert response.status_code == 403
        assert "token" not in response.text.lower()
