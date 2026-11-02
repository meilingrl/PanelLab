"""Durable change claims survive site deletion; site_id deliberately has no FK."""
from datetime import datetime
from typing import Optional
from sqlalchemy import Integer, String, Text, DateTime, Boolean, JSON, BigInteger
from sqlalchemy.orm import Mapped, mapped_column
from database import Base

class SiteChange(Base):
    __tablename__ = 'site_changes'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    jti: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    site_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    site_name: Mapped[str] = mapped_column(String(64), nullable=False)
    operation: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    before: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    after: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    steps: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    simulated: Mapped[bool] = mapped_column(Boolean, nullable=False)
