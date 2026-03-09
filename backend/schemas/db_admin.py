"""数据库管理请求/响应模型（阶段 4）。"""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

# 库名/用户名白名单：字母、数字、下划线（与 Plan 5.3 一致）
NAME_PATTERN = r"^[a-zA-Z0-9_]+$"
ALLOWED_PRIVILEGES = frozenset({
    "SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "DROP",
    "INDEX", "ALTER", "REFERENCES", "EXECUTE", "ALL PRIVILEGES",
})


def _validate_name(value: str, field: str) -> str:
    import re
    v = (value or "").strip()
    if not v:
        raise ValueError(f"{field}不能为空")
    if len(v) > 64:
        raise ValueError(f"{field}长度不能超过 64")
    if not re.match(NAME_PATTERN, v):
        raise ValueError(f"{field}仅允许字母、数字、下划线")
    return v


class DatabaseCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    charset: str = Field(default="utf8mb4", max_length=32)
    collation: str = Field(default="utf8mb4_unicode_ci", max_length=64)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return _validate_name(v, "数据库名")


class DatabaseItem(BaseModel):
    name: str
    charset: Optional[str] = None
    collation: Optional[str] = None
    created_at: Optional[datetime] = None


class DatabaseListResponse(BaseModel):
    items: List[DatabaseItem]


class UserCreate(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=8, max_length=256)
    host: str = Field(default="%", max_length=255)
    database: str = Field(..., min_length=1, max_length=64)
    privileges: List[str] = Field(..., min_length=1)

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        return _validate_name(v, "用户名")

    @field_validator("host")
    @classmethod
    def validate_host(cls, v: str) -> str:
        s = (v or "%").strip() or "%"
        if len(s) > 255:
            raise ValueError("host 长度不能超过 255")
        return s

    @field_validator("database")
    @classmethod
    def validate_database(cls, v: str) -> str:
        return _validate_name(v, "数据库名")

    @field_validator("privileges")
    @classmethod
    def validate_privileges(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("权限列表不能为空")
        out = []
        for p in v:
            pn = (p or "").strip().upper()
            if pn not in ALLOWED_PRIVILEGES:
                raise ValueError(f"不支持的权限: {p}")
            out.append(pn)
        return out


class UserItem(BaseModel):
    user: str
    host: str
    grants_summary: Optional[str] = None


class UserListResponse(BaseModel):
    items: List[UserItem]
