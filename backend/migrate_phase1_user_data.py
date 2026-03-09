"""
阶段一数据迁移：为 site_configs、monitor_remote_config 添加 user_id，并创建 servers 表。
用于已有数据库升级；新环境直接运行 init_db 即可得到完整表结构。

运行方式（在 backend 目录下）：
  python migrate_phase1_user_data.py

依赖：需先存在 users 表且至少有一条用户（如已运行 init_db）。
"""
import sys
from sqlalchemy import text
from database import engine
from models import User, Server


def run_migration():
    with engine.connect() as conn:
        # 获取首个用户 id，用于回填
        result = conn.execute(text("SELECT id FROM users ORDER BY id LIMIT 1"))
        row = result.fetchone()
        if not row:
            print("错误：未找到任何用户，请先运行 python -m init_db 创建管理员。")
            return False
        default_user_id = row[0]
        print(f"将把现有数据关联到 user_id={default_user_id}")

        # ----- site_configs -----
        try:
            conn.execute(text("ALTER TABLE site_configs ADD COLUMN user_id INT NULL"))
            conn.commit()
            print("site_configs: 已添加 user_id 列")
        except Exception as e:
            if "Duplicate column" in str(e) or "already exists" in str(e).lower():
                print("site_configs: user_id 列已存在，跳过")
            else:
                conn.rollback()
                raise

        conn.execute(text("UPDATE site_configs SET user_id = :uid WHERE user_id IS NULL"), {"uid": default_user_id})
        conn.commit()

        try:
            conn.execute(text("ALTER TABLE site_configs MODIFY COLUMN user_id INT NOT NULL"))
            conn.commit()
        except Exception as e:
            conn.rollback()
            if "Duplicate column" in str(e):
                pass
            else:
                raise

        try:
            conn.execute(text(
                "ALTER TABLE site_configs ADD CONSTRAINT fk_site_configs_user "
                "FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE"
            ))
            conn.commit()
            print("site_configs: 已设置 user_id NOT NULL 与外键")
        except Exception as e:
            conn.rollback()
            if "Duplicate" in str(e) or "already exists" in str(e).lower():
                print("site_configs: 外键已存在，跳过")
            else:
                raise

        # 删除旧唯一约束并添加按用户唯一的约束（MySQL 唯一索引名多为列名）
        for index_name in ("name", "domain", "ix_site_configs_name", "ix_site_configs_domain"):
            try:
                conn.execute(text(f"ALTER TABLE site_configs DROP INDEX {index_name}"))
                conn.commit()
                print(f"site_configs: 已删除旧索引 {index_name}")
            except Exception as e:
                conn.rollback()
                if "check that it exists" in str(e).lower() or "1091" in str(e) or "Can't DROP" in str(e):
                    pass
                else:
                    print(f"site_configs: 删除索引 {index_name} 时: {e}")

        for name, cols in (("uq_site_configs_user_name", "user_id, name"), ("uq_site_configs_user_domain", "user_id, domain")):
            try:
                conn.execute(text(f"ALTER TABLE site_configs ADD UNIQUE INDEX {name} ({cols})"))
                conn.commit()
                print(f"site_configs: 已添加唯一索引 {name}")
            except Exception as e:
                conn.rollback()
                if "Duplicate" in str(e) or "already exists" in str(e).lower():
                    print(f"site_configs: 索引 {name} 已存在，跳过")
                else:
                    raise

        # ----- monitor_remote_config -----
        try:
            conn.execute(text("ALTER TABLE monitor_remote_config ADD COLUMN user_id INT NULL"))
            conn.commit()
            print("monitor_remote_config: 已添加 user_id 列")
        except Exception as e:
            if "Duplicate column" in str(e) or "already exists" in str(e).lower():
                print("monitor_remote_config: user_id 列已存在，跳过")
            else:
                conn.rollback()
                raise

        conn.execute(text("UPDATE monitor_remote_config SET user_id = :uid WHERE user_id IS NULL"), {"uid": default_user_id})
        conn.commit()

        try:
            conn.execute(text("ALTER TABLE monitor_remote_config MODIFY COLUMN user_id INT NOT NULL"))
            conn.commit()
        except Exception:
            conn.rollback()

        try:
            conn.execute(text(
                "ALTER TABLE monitor_remote_config ADD CONSTRAINT fk_monitor_remote_config_user "
                "FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE"
            ))
            conn.commit()
            print("monitor_remote_config: 已设置 user_id NOT NULL 与外键")
        except Exception as e:
            conn.rollback()
            if "Duplicate" in str(e) or "already exists" in str(e).lower():
                print("monitor_remote_config: 外键已存在，跳过")
            else:
                raise

        # ----- servers 表（新建）-----
        Server.__table__.create(engine, checkfirst=True)
        print("servers: 表已创建或已存在")

    return True


if __name__ == "__main__":
    try:
        ok = run_migration()
        sys.exit(0 if ok else 1)
    except Exception as e:
        print("迁移失败:", e)
        sys.exit(1)
