"""Additive private-key record for one server; a new table needs no column migration."""
from datetime import datetime
from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


class ServerPrivateKey(Base):
    __tablename__ = 'server_private_keys'
    server_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('servers.id', ondelete='CASCADE'), primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    # Unencrypted OpenSSH text, Fernet-encrypted with SECRET_KEY. The upload passphrase is never stored.
    key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    key_type: Mapped[str] = mapped_column(String(64), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(100), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
