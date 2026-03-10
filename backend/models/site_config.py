"""站点配置模型。"""
from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class SiteConfig(Base):
    __tablename__ = "site_configs"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_site_configs_user_name"),
        UniqueConstraint("user_id", "domain", name="uq_site_configs_user_domain"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    site_type: Mapped[str] = mapped_column(String(16), nullable=False)  # static | proxy
    root_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    proxy_target: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    listen_port: Mapped[int] = mapped_column(Integer, nullable=False, default=80)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    config_filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")  # draft | active | error
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
