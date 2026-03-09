"""MySQL 管理：库/用户列表、创建、删除（阶段 4）。"""
from __future__ import annotations

import re
from typing import List, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

from config import get_settings
from database import engine

ALLOWED_PRIVILEGES = frozenset({
    "SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "DROP",
    "INDEX", "ALTER", "REFERENCES", "EXECUTE", "ALL PRIVILEGES",
})

# 禁止操作的系统库（与 Plan 5.3 一致）
PROTECTED_DATABASES = frozenset({
    "mysql", "information_schema", "performance_schema", "sys",
})
# 业务库名（当前应用库，禁止删除）
APP_DATABASE_KEY = "mysql_database"


class MysqlAdminError(Exception):
    """MySQL 管理可预期异常，对应 503 或 409。"""

    def __init__(self, detail: str, status_code: int = 503):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def _get_protected_databases() -> frozenset:
    s = get_settings()
    protected = set(PROTECTED_DATABASES)
    if hasattr(s, APP_DATABASE_KEY) and getattr(s, APP_DATABASE_KEY):
        protected.add(getattr(s, APP_DATABASE_KEY).lower())
    return frozenset(protected)


def _validate_db_name(name: str) -> None:
    if not name or not re.match(r"^[a-zA-Z0-9_]+$", name):
        raise MysqlAdminError("数据库名仅允许字母、数字、下划线", status_code=400)
    if name.lower() in _get_protected_databases():
        raise MysqlAdminError("不允许操作系统库或当前应用库", status_code=400)


def _execute(eng: Engine, sql: str, params: Optional[dict] = None):
    with eng.connect() as conn:
        conn.execute(text(sql), params or {})
        conn.commit()


def _execute_scalars(eng: Engine, sql: str, params: Optional[dict] = None):
    with eng.connect() as conn:
        result = conn.execute(text(sql), params or {})
        return result.fetchall()


def list_databases(eng: Engine = engine) -> List[dict]:
    """返回可管理数据库列表，排除系统库与当前应用库。"""
    protected = _get_protected_databases()
    # information_schema.SCHEMATA 包含 SCHEMA_NAME, DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME
    # 部分版本无创建时间；有则用 CREATE_TIME
    sql = """
    SELECT SCHEMA_NAME AS name,
           DEFAULT_CHARACTER_SET_NAME AS charset,
           DEFAULT_COLLATION_NAME AS collation
    FROM information_schema.SCHEMATA
    ORDER BY SCHEMA_NAME
    """
    rows = _execute_scalars(eng, sql)
    out = []
    for row in rows:
        name = (row[0] or "").strip()
        if not name or name.lower() in protected:
            continue
        out.append({
            "name": name,
            "charset": row[1] if len(row) > 1 else None,
            "collation": row[2] if len(row) > 2 else None,
            "created_at": None,
        })
    return out


def create_database(
    name: str,
    charset: str = "utf8mb4",
    collation: str = "utf8mb4_unicode_ci",
    eng: Engine = engine,
) -> None:
    """创建数据库。名称已由 schema 校验，此处再次校验并禁止系统库。"""
    _validate_db_name(name)
    # 标识符用反引号包裹，charset/collation 用占位或校验后拼接（MySQL 不支持 ? 占位符用于 charset）
    safe_charset = re.sub(r"[^a-zA-Z0-9_]", "", charset) or "utf8mb4"
    safe_collation = re.sub(r"[^a-zA-Z0-9_]", "", collation) or "utf8mb4_unicode_ci"
    sql = f"CREATE DATABASE `{name}` CHARACTER SET {safe_charset} COLLATE {safe_collation}"
    try:
        _execute(eng, sql)
    except Exception as e:
        msg = str(e).strip()
        if "1007" in msg or "exists" in msg.lower():
            raise MysqlAdminError("数据库已存在", status_code=409)
        raise MysqlAdminError(f"创建数据库失败: {msg}")


def delete_database(name: str, eng: Engine = engine) -> None:
    """删除数据库。"""
    _validate_db_name(name)
    sql = f"DROP DATABASE `{name}`"
    try:
        _execute(eng, sql)
    except Exception as e:
        msg = str(e).strip()
        if "1008" in msg or "doesn't exist" in msg.lower():
            raise MysqlAdminError("数据库不存在", status_code=404)
        raise MysqlAdminError(f"删除数据库失败: {msg}")


def list_users(eng: Engine = engine) -> List[dict]:
    """返回用户列表（user, host）。"""
    sql = "SELECT User, Host FROM mysql.user ORDER BY User, Host"
    try:
        rows = _execute_scalars(eng, sql)
    except Exception as e:
        raise MysqlAdminError(f"获取用户列表失败: {str(e).strip()}")
    return [{"user": row[0] or "", "host": row[1] or "", "grants_summary": None} for row in rows]


def _validate_user_host(s: str, label: str) -> str:
    s = (s or "").strip()
    if not s:
        raise MysqlAdminError(f"{label}不能为空", status_code=400)
    # host 允许 % 和字母数字、点、下划线
    if not re.match(r"^[%a-zA-Z0-9._]+$", s):
        raise MysqlAdminError(f"{label}仅允许字母、数字、%、点、下划线", status_code=400)
    return s


def _quote_user_host(user: str, host: str) -> str:
    """MySQL 要求 'user'@'host'，已校验仅含安全字符，直接拼接。"""
    return f"'{user}'@'{host}'"


def create_user(
    username: str,
    password: str,
    host: str,
    database: str,
    privileges: List[str],
    eng: Engine = engine,
) -> None:
    """创建用户并授权。"""
    username = _validate_user_host(username, "用户名")
    host = _validate_user_host(host, "host")
    if not re.match(r"^[a-zA-Z0-9_]+$", database):
        raise MysqlAdminError("数据库名仅允许字母、数字、下划线", status_code=400)
    if not privileges:
        raise MysqlAdminError("权限列表不能为空", status_code=400)
    priv_list = ", ".join(p for p in privileges if p.upper() in ALLOWED_PRIVILEGES)
    if not priv_list:
        raise MysqlAdminError("无有效权限项", status_code=400)
    uid = _quote_user_host(username, host)
    try:
        # CREATE USER 'u'@'h' IDENTIFIED BY :password（密码用占位符）
        create_sql = f"CREATE USER {uid} IDENTIFIED BY :password"
        with eng.connect() as conn:
            conn.execute(text(create_sql), {"password": password})
            conn.commit()
        grant_sql = f"GRANT {priv_list} ON `{database}`.* TO {uid}"
        with eng.connect() as conn:
            conn.execute(text(grant_sql))
            conn.commit()
        _execute(eng, "FLUSH PRIVILEGES")
    except Exception as e:
        msg = str(e).strip()
        if "1396" in msg or "exists" in msg.lower() or "already exists" in msg.lower():
            raise MysqlAdminError("用户已存在", status_code=409)
        raise MysqlAdminError(f"创建用户失败: {msg}")


def drop_user(username: str, host: str = "%", eng: Engine = engine) -> None:
    """删除用户。"""
    username = _validate_user_host(username, "用户名")
    host = (host or "%").strip() or "%"
    if not re.match(r"^[%a-zA-Z0-9._]+$", host):
        raise MysqlAdminError("host 仅允许字母、数字、%、点、下划线", status_code=400)
    uid = _quote_user_host(username, host)
    sql = f"DROP USER {uid}"
    try:
        with eng.connect() as conn:
            conn.execute(text(sql))
            conn.commit()
        _execute(eng, "FLUSH PRIVILEGES")
    except Exception as e:
        msg = str(e).strip()
        if "1396" in msg or "doesn't exist" in msg.lower():
            raise MysqlAdminError("用户不存在", status_code=404)
        raise MysqlAdminError(f"删除用户失败: {msg}")
