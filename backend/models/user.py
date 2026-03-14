"""用户模型。"""
import os
from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base

# SQLite 下用 Integer 才能正确自增；MySQL 用 BigInteger 与其它表 user_id 一致
_ID_TYPE = Integer if (os.environ.get("DATABASE_URL") or "").startswith("sqlite") else BigInteger


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(_ID_TYPE, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(String(20), unique=True, nullable=True, index=True)
    wechat_openid: Mapped[Optional[str]] = mapped_column(String(64), unique=True, nullable=True, index=True)
    qq_openid: Mapped[Optional[str]] = mapped_column(String(64), unique=True, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
