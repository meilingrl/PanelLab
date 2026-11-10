"""应用配置，从环境变量加载。"""
from typing import Literal, Optional

from pydantic import model_validator

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "panel_lab"
    mysql_password: str = ""
    mysql_database: str = "panel_lab"
    database_url: Optional[str] = None

    app_env: Literal["development", "test", "production"] = "development"
    enable_demo_auth: bool = False
    operator_usernames: str = "admin"
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

    # 网站与反向代理（阶段 3）
    nginx_conf_dir: str = "/etc/nginx/conf.d"
    nginx_test_cmd: str = "nginx -t"
    nginx_reload_cmd: str = "nginx -s reload"
    nginx_cmd_timeout_seconds: int = 15
    nginx_skip_apply: bool = False

    # 多途径登录：微信开放平台 / QQ 互联（不配置则走 mock）
    wechat_app_id: str = ""
    wechat_app_secret: str = ""
    wechat_redirect_uri: str = ""  # 后端回调完整 URL，如 http://localhost:8000/api/auth/wechat/callback
    qq_app_id: str = ""
    qq_app_key: str = ""
    qq_redirect_uri: str = ""  # 如 http://localhost:8000/api/auth/qq/callback
    frontend_origin: str = "http://localhost:5173"  # 回调成功后跳转的前端地址，用于带 token 重定向

    @model_validator(mode="after")
    def validate_production(self):
        if self.app_env == "production":
            if len(self.secret_key) < 32 or self.secret_key.strip().lower() in {
                "change-me-in-production", "your-secret-key-change-in-production"
            }:
                raise ValueError("production requires a non-example secret key of at least 32 characters")
            if len(self.init_admin_password) < 12 or self.init_admin_password.strip().lower() == "admin":
                raise ValueError("production requires a non-default initial password of at least 12 characters")
            if self.nginx_skip_apply or self.enable_demo_auth:
                raise ValueError("production disallows nginx_skip_apply and demo authentication")
        return self


def get_settings() -> Settings:
    return Settings()
