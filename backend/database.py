"""数据库连接与会话。"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from config import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()

if _settings.database_url:
    url = _settings.database_url
else:
    url = (
        f"mysql+pymysql://{_settings.mysql_user}:{_settings.mysql_password}"
        f"@{_settings.mysql_host}:{_settings.mysql_port}/{_settings.mysql_database}"
    )

engine = create_engine(url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
