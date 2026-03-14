"""
多途径登录迁移：为 users 表增加 phone、wechat_openid、qq_openid 字段。
用于已有 MySQL 数据库升级；新环境直接启动应用时 create_all 会建齐表（若模型已含这些列）。

运行方式（在 backend 目录下）：
  python migrate_login_multi.py

注意：本脚本会连接 .env 中配置的 MySQL。若本机未启动 MySQL，会报连接失败。
  - 使用 MySQL 时：请先启动 MySQL 服务后再执行本脚本。
  - 仅本地开发且用 SQLite 时：无需运行本脚本，设置 DATABASE_URL=sqlite:///./local.db 后
    启动应用即可，create_all 会创建包含新列的完整表结构。
"""
import sys
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from database import engine


def run_migration():
    with engine.connect() as conn:
        dialect = conn.dialect.name
        if dialect == "sqlite":
            col_specs = (
                ("phone", "VARCHAR(20) UNIQUE"),
                ("wechat_openid", "VARCHAR(64) UNIQUE"),
                ("qq_openid", "VARCHAR(64) UNIQUE"),
            )
        else:
            col_specs = (
                ("phone", "VARCHAR(20) NULL UNIQUE"),
                ("wechat_openid", "VARCHAR(64) NULL UNIQUE"),
                ("qq_openid", "VARCHAR(64) NULL UNIQUE"),
            )
        for col, col_type in col_specs:
            try:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} {col_type}"))
                conn.commit()
                print(f"users: 已添加列 {col}")
            except Exception as e:
                conn.rollback()
                if "Duplicate column" in str(e) or "1060" in str(e) or "duplicate column name" in str(e).lower():
                    print(f"users: 列 {col} 已存在，跳过")
                else:
                    print(f"users: 添加列 {col} 时 {e}")
    return True


if __name__ == "__main__":
    try:
        run_migration()
    except OperationalError as e:
        print("迁移失败: 无法连接数据库。")
        print("  - 若使用 MySQL：请先启动 MySQL 服务后再执行本脚本。")
        print("  - 若仅本地开发：可设置环境变量 DATABASE_URL=sqlite:///./local.db 后重试，或直接启动应用（create_all 会建齐表）。")
        print("  原始错误:", e)
        sys.exit(1)
    except Exception as e:
        print("迁移失败:", e)
        sys.exit(1)
