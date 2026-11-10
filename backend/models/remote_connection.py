"""Additive trust record; changing a Server target invalidates its binding."""
from sqlalchemy import BigInteger, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


class RemoteConnection(Base):
    __tablename__ = 'remote_connections'
    server_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('servers.id', ondelete='CASCADE'), primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    key_type: Mapped[str] = mapped_column(String(64), nullable=False)
    key_data: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(100), nullable=False)
    auth_mode: Mapped[str] = mapped_column(String(16), nullable=False)
