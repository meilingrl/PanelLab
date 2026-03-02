"""创建表并插入初始管理员（仅当无用户时）。运行方式：在 backend 目录下 python -m init_db"""
import sys
from database import engine, SessionLocal, Base
from models.user import User
from config import get_settings
from passlib.context import CryptContext

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


def main():
    settings = get_settings()
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if db.query(User).first() is not None:
            print("已存在用户，跳过初始化。")
            return
        admin_password = settings.init_admin_password
        admin = User(
            username="admin",
            password_hash=pwd_ctx.hash(admin_password),
        )
        db.add(admin)
        db.commit()
        print("已创建初始管理员：用户名 admin，密码为 .env 中 INIT_ADMIN_PASSWORD（默认 admin）。")
    finally:
        db.close()


if __name__ == "__main__":
    main()
    sys.exit(0)
