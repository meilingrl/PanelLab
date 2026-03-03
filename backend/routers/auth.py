"""认证：登录、登出、当前用户。"""
from datetime import datetime, timedelta
from typing import Optional

try:
    from typing import Annotated
except ImportError:
    from typing_extensions import Annotated

import jwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from config import get_settings
from database import get_db
from models.user import User
from schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    UserMe,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])
security = HTTPBearer(auto_error=False)
pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _hash_password(password: str) -> str:
    return pwd_ctx.hash(password)


def _verify_password(plain: str, hashed: str) -> bool:
    return pwd_ctx.verify(plain, hashed)


def _create_token(username: str) -> str:
    settings = get_settings()
    payload = {
        "sub": username,
        "exp": datetime.utcnow() + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def _decode_token(token: str) -> Optional[str]:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
        return payload.get("sub")
    except Exception:
        return None


def get_current_user(
    credentials: Annotated[Optional[HTTPAuthorizationCredentials], Depends(security)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    if not credentials or not credentials.credentials:
        raise HTTPException(status_code=401, detail="未提供认证信息")
    username = _decode_token(credentials.credentials)
    if not username:
        raise HTTPException(status_code=401, detail="认证已失效，请重新登录")
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    return user


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, db: Annotated[Session, Depends(get_db)]):
    """登录：校验用户名密码，返回 JWT 与用户信息。"""
    user = db.query(User).filter(User.username == body.username).first()
    if not user or not _verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = _create_token(user.username)
    return LoginResponse(token=token, user=UserMe(username=user.username))


@router.post("/logout")
def logout():
    """登出：前端删除 token 即可；后端可选做 token 黑名单。"""
    return {"message": "ok"}


@router.get("/me", response_model=UserMe)
def me(current_user: Annotated[User, Depends(get_current_user)]):
    """获取当前登录用户信息。"""
    return UserMe(username=current_user.username)


@router.post("/register", response_model=LoginResponse)
def register(body: RegisterRequest, db: Annotated[Session, Depends(get_db)]):
    """注册新用户；成功后直接返回 token 与用户信息，可视为登录。"""
    username = body.username.strip()
    if len(username) < 2:
        raise HTTPException(status_code=400, detail="用户名至少 2 个字符")
    existing = db.query(User).filter(User.username == username).first()
    if existing:
        raise HTTPException(status_code=400, detail="用户名已被使用")
    user = User(
        username=username,
        password_hash=_hash_password(body.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    token = _create_token(user.username)
    return LoginResponse(token=token, user=UserMe(username=user.username))


@router.post("/change-password")
def change_password(
    body: ChangePasswordRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """登录状态下修改当前用户密码。"""
    if not _verify_password(body.old_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="原密码错误")
    if body.old_password == body.new_password:
        raise HTTPException(status_code=400, detail="新密码不能与原密码相同")
    current_user.password_hash = _hash_password(body.new_password)
    db.commit()
    return {"message": "密码已更新，请使用新密码登录"}
