"""应用配置，从环境变量加载。"""
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "panel_lab"
    mysql_password: str = ""
    mysql_database: str = "panel_lab"
    database_url: Optional[str] = None

    app_env: str = "development"
    secret_key: str = "change-me-in-production"
    # 首次运行时的默认管理员密码（仅当库内无用户时写入）
    init_admin_password: str = "admin"
    jwt_expire_minutes: int = 60 * 24 * 7  # 7 天

    # 远程 Linux 监控（可选）
    remote_ssh_host: str = ""
    remote_ssh_port: int = 22
    remote_ssh_user: str = ""
    remote_ssh_key_path: str = ""
    remote_ssh_password: str = ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


def get_settings() -> Settings:
    return Settings()
