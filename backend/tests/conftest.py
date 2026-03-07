"""Pytest 配置：使用 SQLite 并注入测试用户，无需 MySQL。"""
import os
from datetime import datetime

# 文件型 SQLite 保证所有连接（conftest 与 app 请求）看到同一库
_test_db = os.path.join(os.path.dirname(__file__), "test.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_test_db}"

import pytest
import bcrypt
from database import Base, SessionLocal, engine
from models.user import User
from main import app
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def db_setup():
    if os.path.exists(_test_db):
        os.remove(_test_db)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        db.add(User(
            username="admin",
            password_hash=bcrypt.hashpw(b"admin", bcrypt.gensalt()).decode("ascii"),
            created_at=now,
            updated_at=now,
        ))
        db.commit()
    finally:
        db.close()
    yield
    try:
        if os.path.exists(_test_db):
            os.remove(_test_db)
    except Exception:
        pass


@pytest.fixture
def client(db_setup):
    return TestClient(app)
