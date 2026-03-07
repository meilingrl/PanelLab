"""修改已存在用户的登录密码。运行方式：在 backend 目录下 python change_password.py [用户名] [新密码]"""
import getpass
import sys
from database import SessionLocal
from models.user import User
import bcrypt


def main():
    if len(sys.argv) >= 3:
        username = sys.argv[1]
        new_password = sys.argv[2]
    else:
        username = "admin"
        if len(sys.argv) == 2:
            username = sys.argv[1]
        new_password = getpass.getpass("请输入新密码: ")
        if not new_password:
            print("密码不能为空。")
            sys.exit(1)

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if not user:
            print(f"用户 {username} 不存在。")
            sys.exit(1)
        user.password_hash = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
        db.commit()
        print(f"已更新用户 {username} 的密码，请使用新密码登录。")
    finally:
        db.close()


if __name__ == "__main__":
    main()
