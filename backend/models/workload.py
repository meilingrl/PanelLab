"""Additive durable workload metadata and explicitly reported runs."""
from datetime import datetime
from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base

ID = BigInteger().with_variant(Integer, 'sqlite')


class Workload(Base):
    __tablename__ = 'workloads'
    id: Mapped[int] = mapped_column(ID, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('users.id', ondelete='CASCADE'), index=True)
    server_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('servers.id', ondelete='RESTRICT'), index=True)
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(20))
    root_path: Mapped[str] = mapped_column(Text)
    log_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    process_identity: Mapped[str | None] = mapped_column(String(128), nullable=True)
    website_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    schedule_note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    runs: Mapped[list['ReportedRun']] = relationship(back_populates='workload', cascade='all, delete-orphan')


class ReportedRun(Base):
    __tablename__ = 'reported_runs'
    id: Mapped[int] = mapped_column(ID, primary_key=True, autoincrement=True)
    workload_id: Mapped[int] = mapped_column(ID, ForeignKey('workloads.id', ondelete='CASCADE'), index=True)
    reported_by: Mapped[int] = mapped_column(BigInteger, ForeignKey('users.id', ondelete='CASCADE'))
    source: Mapped[str] = mapped_column(String(20), default='reported')
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20))
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    workload: Mapped[Workload] = relationship(back_populates='runs')
