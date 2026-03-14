"""认证：登录、登出、当前用户；多途径登录（手机验证码、微信/QQ 扫码，可配置真实 OAuth）。"""
import re
import secrets
import time
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import quote

try:
    from typing import Annotated
except ImportError:
    from typing_extensions import Annotated

import bcrypt
import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config import get_settings
from database import get_db
from models.user import User
from schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    SmsLoginRequest,
    SmsSendRequest,
    UserMe,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])
security = HTTPBearer(auto_error=False)

# 手机验证码：内存存储（phone -> {code, expires_at}）与节流（phone -> last_send_ts）
_sms_store: dict = {}
_sms_throttle: dict = {}
SMS_CODE_EXPIRE_SECONDS = 300
SMS_THROTTLE_SECONDS = 60
MOCK_SMS_CODE = "123456"


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def _verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("ascii"))
    except Exception:
        return False


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


def _password_placeholder() -> str:
    """无密码用户（仅手机/第三方登录）占位哈希，不可用于登录。"""
    return bcrypt.hashpw(b"no-password-placeholder", bcrypt.gensalt()).decode("ascii")


def _ensure_unique_username(db: Session, base_username: str) -> str:
    """保证 username 唯一：先试 base，冲突则加 4 位 hex 后缀重试。"""
    username = base_username
    for _ in range(3):
        if db.query(User).filter(User.username == username).first() is None:
            return username
        username = f"{base_username}_{secrets.token_hex(2)}"
    return username


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


# ---------- 手机验证码（mock）----------

def _phone_valid(phone: str) -> bool:
    return bool(re.match(r"^1\d{10}$", phone))


@router.post("/sms/send")
def sms_send(body: SmsSendRequest):
    """发送验证码（mock：固定 123456）；同一手机 60s 内仅可发送一次。"""
    phone = body.phone.strip()
    if not _phone_valid(phone):
        raise HTTPException(status_code=400, detail="手机号格式不正确")
    now = time.time()
    if _sms_throttle.get(phone, 0) + SMS_THROTTLE_SECONDS > now:
        raise HTTPException(status_code=429, detail="发送过于频繁，请稍后再试")
    _sms_store[phone] = {"code": MOCK_SMS_CODE, "expires_at": now + SMS_CODE_EXPIRE_SECONDS}
    _sms_throttle[phone] = now
    return {"message": "验证码已发送"}


@router.post("/sms/login", response_model=LoginResponse)
def sms_login(body: SmsLoginRequest, db: Annotated[Session, Depends(get_db)]):
    """验证码登录；用户不存在则自动创建（username=phone_xxx）。"""
    phone = body.phone.strip()
    if not _phone_valid(phone):
        raise HTTPException(status_code=400, detail="手机号格式不正确")
    entry = _sms_store.get(phone)
    if not entry or time.time() > entry["expires_at"]:
        raise HTTPException(status_code=401, detail="验证码已过期，请重新获取")
    if entry["code"] != body.code:
        raise HTTPException(status_code=401, detail="验证码错误")
    del _sms_store[phone]
    user = db.query(User).filter(User.phone == phone).first()
    if not user:
        base_username = f"phone_{phone}"
        username = _ensure_unique_username(db, base_username)
        user = User(
            username=username,
            password_hash=_password_placeholder(),
            phone=phone,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    token = _create_token(user.username)
    return LoginResponse(token=token, user=UserMe(username=user.username))


# ---------- 微信扫码（配置 WECHAT_APP_ID/SECRET/REDIRECT_URI 为真实扫码，否则 mock）----------

MOCK_WECHAT_OPENID = "mock_wx_001"

# 真实扫码时：手机端会打开回调页，PC 端通过轮询此 dict 拿到 token
_wechat_pending: dict = {}  # state -> {"token": str, "username": str}

WECHAT_QR_BASE = "https://open.weixin.qq.com/connect/qrconnect"
WECHAT_TOKEN_URL = "https://api.weixin.qq.com/sns/oauth2/access_token"


def _wechat_oauth_configured() -> bool:
    s = get_settings()
    return bool(s.wechat_app_id and s.wechat_app_secret and s.wechat_redirect_uri)


def _redirect_frontend_with_token(token: str, username: str, error: Optional[str] = None) -> RedirectResponse:
    """重定向到前端登录页并带 token（或 error）于 hash，避免 token 进服务端日志。"""
    s = get_settings()
    origin = (s.frontend_origin or "").rstrip("/")
    base = f"{origin}/login"
    if error:
        return RedirectResponse(url=f"{base}#error={quote(error)}", status_code=302)
    return RedirectResponse(url=f"{base}#token={quote(token)}&username={quote(username)}", status_code=302)


@router.get("/wechat/qr")
def wechat_qr():
    """获取微信扫码授权 URL；已配置则返回真实 qrconnect URL，否则 mock。"""
    state = secrets.token_urlsafe(16)
    s = get_settings()
    if _wechat_oauth_configured():
        redirect_uri = quote(s.wechat_redirect_uri, safe="")
        qr_url = (
            f"{WECHAT_QR_BASE}?appid={quote(s.wechat_app_id)}"
            f"&redirect_uri={redirect_uri}&response_type=code&scope=snsapi_login&state={state}#wechat_redirect"
        )
        return {"qr_url": qr_url, "state": state, "mock": False}
    return {"qr_url": f"{WECHAT_QR_BASE}?state={state}", "state": state, "mock": True}


def _wechat_exchange_code_for_openid(code: str) -> Optional[str]:
    """用 code 向微信换 access_token 与 openid，失败返回 None。"""
    s = get_settings()
    url = (
        f"{WECHAT_TOKEN_URL}?appid={quote(s.wechat_app_id)}&secret={quote(s.wechat_app_secret)}"
        f"&code={quote(code)}&grant_type=authorization_code"
    )
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.get(url)
            data = r.json()
    except Exception:
        return None
    return data.get("openid") if isinstance(data, dict) and "errcode" not in data else None


def _wechat_find_or_create_user(db: Session, openid: str) -> Optional[User]:
    """按 openid 查用户，无则创建；返回 User 或 None。"""
    user = db.query(User).filter(User.wechat_openid == openid).first()
    if user:
        return user
    base_username = f"wx_{openid[-8:]}" if len(openid) >= 8 else f"wx_{openid}"
    username = _ensure_unique_username(db, base_username)
    user = User(
        username=username,
        password_hash=_password_placeholder(),
        wechat_openid=openid,
    )
    db.add(user)
    try:
        db.commit()
        db.refresh(user)
        return user
    except IntegrityError:
        db.rollback()
        return db.query(User).filter(User.wechat_openid == openid).first()


@router.get("/wechat/callback")
def wechat_callback_get(
    db: Annotated[Session, Depends(get_db)],
    code: Optional[str] = None,
    state: Optional[str] = None,
):
    """微信授权回调（GET）：扫码后在手机端打开，用 code 换 openid，结果存 state 供 PC 轮询；返回「登录成功请关闭」页。"""
    s = get_settings()
    frontend_origin = (s.frontend_origin or "").rstrip("/")
    error_page = f"{frontend_origin}/login#error=微信授权失败"
    if not code:
        return RedirectResponse(url=f"{frontend_origin}/login#error={quote('微信授权未返回 code')}", status_code=302)
    openid = None
    if _wechat_oauth_configured():
        openid = _wechat_exchange_code_for_openid(code)
    if not openid:
        openid = MOCK_WECHAT_OPENID
    user = _wechat_find_or_create_user(db, openid)
    if not user:
        return RedirectResponse(url=error_page, status_code=302)
    token = _create_token(user.username)
    if state:
        _wechat_pending[state] = {"token": token, "username": user.username}
    html = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'><title>登录成功</title></head><body>"
        "<p>登录成功，请关闭此页面返回电脑端。</p></body></html>"
    )
    return HTMLResponse(html)


@router.get("/wechat/poll")
def wechat_poll(state: Optional[str] = None):
    """PC 端轮询：扫码并确认后，凭 state 取回 token；有则返回 200+token，无则 202。"""
    if not state or state not in _wechat_pending:
        return JSONResponse(content={"detail": "等待扫码"}, status_code=202)
    data = _wechat_pending.pop(state)
    return LoginResponse(token=data["token"], user=UserMe(username=data["username"]))


@router.post("/wechat/callback", response_model=LoginResponse)
def wechat_callback_post(
    db: Annotated[Session, Depends(get_db)],
    code: Optional[str] = None,
    state: Optional[str] = None,
):
    """微信授权回调（POST）：前端「模拟扫码」时调用，返回 JSON。"""
    openid = MOCK_WECHAT_OPENID
    user = db.query(User).filter(User.wechat_openid == openid).first()
    if not user:
        user = _wechat_find_or_create_user(db, openid)
    if not user:
        raise HTTPException(status_code=500, detail="创建用户失败，请重试")
    token = _create_token(user.username)
    return LoginResponse(token=token, user=UserMe(username=user.username))


# ---------- QQ 扫码（配置 QQ_APP_ID/APP_KEY/REDIRECT_URI 为真实，否则 mock）----------

MOCK_QQ_OPENID = "mock_qq_001"

QQ_AUTHORIZE_URL = "https://graph.qq.com/oauth2.0/authorize"
QQ_TOKEN_URL = "https://graph.qq.com/oauth2.0/token"
QQ_ME_URL = "https://graph.qq.com/oauth2.0/me"


def _qq_oauth_configured() -> bool:
    s = get_settings()
    return bool(s.qq_app_id and s.qq_app_key and s.qq_redirect_uri)


def _qq_exchange_code_for_access_token(code: str) -> Optional[str]:
    s = get_settings()
    url = (
        f"{QQ_TOKEN_URL}?grant_type=authorization_code&client_id={quote(s.qq_app_id)}"
        f"&client_secret={quote(s.qq_app_key)}&code={quote(code)}&redirect_uri={quote(s.qq_redirect_uri)}&fmt=json"
    )
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.get(url)
            data = r.json()
    except Exception:
        return None
    if isinstance(data, dict) and "access_token" in data:
        return data["access_token"]
    return None


def _qq_get_openid(access_token: str) -> Optional[str]:
    url = f"{QQ_ME_URL}?access_token={quote(access_token)}&fmt=json"
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.get(url)
            data = r.json()
    except Exception:
        return None
    if isinstance(data, dict) and "openid" in data:
        return data["openid"]
    return None


def _qq_find_or_create_user(db: Session, openid: str) -> Optional[User]:
    user = db.query(User).filter(User.qq_openid == openid).first()
    if user:
        return user
    base_username = f"qq_{openid[-8:]}" if len(openid) >= 8 else f"qq_{openid}"
    username = _ensure_unique_username(db, base_username)
    user = User(
        username=username,
        password_hash=_password_placeholder(),
        qq_openid=openid,
    )
    db.add(user)
    try:
        db.commit()
        db.refresh(user)
        return user
    except IntegrityError:
        db.rollback()
        return db.query(User).filter(User.qq_openid == openid).first()


@router.get("/qq/url")
def qq_url():
    """获取 QQ 互联授权 URL；已配置则返回真实 authorize URL，否则 mock。"""
    state = secrets.token_urlsafe(16)
    s = get_settings()
    if _qq_oauth_configured():
        auth_url = (
            f"{QQ_AUTHORIZE_URL}?response_type=code&client_id={quote(s.qq_app_id)}"
            f"&redirect_uri={quote(s.qq_redirect_uri)}&state={state}"
        )
        return {"auth_url": auth_url, "state": state, "mock": False}
    return {"auth_url": f"{QQ_AUTHORIZE_URL}?state={state}", "state": state, "mock": True}


@router.get("/qq/callback")
def qq_callback_get(
    db: Annotated[Session, Depends(get_db)],
    code: Optional[str] = None,
    state: Optional[str] = None,
):
    """QQ 授权回调（GET）：用户同意授权后重定向到此，换 token 与 openid 并跳转前端。"""
    if not code:
        return _redirect_frontend_with_token("", "", error="QQ 授权未返回 code")
    if _qq_oauth_configured():
        access_token = _qq_exchange_code_for_access_token(code)
        if not access_token:
            return _redirect_frontend_with_token("", "", error="QQ 授权失败，请重试")
        openid = _qq_get_openid(access_token)
        if not openid:
            return _redirect_frontend_with_token("", "", error="获取 QQ 用户信息失败，请重试")
        user = _qq_find_or_create_user(db, openid)
        if not user:
            return _redirect_frontend_with_token("", "", error="登录失败，请重试")
        token = _create_token(user.username)
        return _redirect_frontend_with_token(token, user.username)
    openid = MOCK_QQ_OPENID
    user = _qq_find_or_create_user(db, openid)
    if not user:
        return _redirect_frontend_with_token("", "", error="登录失败，请重试")
    token = _create_token(user.username)
    return _redirect_frontend_with_token(token, user.username)


@router.post("/qq/callback", response_model=LoginResponse)
def qq_callback_post(
    db: Annotated[Session, Depends(get_db)],
    code: Optional[str] = None,
    state: Optional[str] = None,
):
    """QQ 授权回调（POST）：前端「模拟扫码」时调用。"""
    openid = MOCK_QQ_OPENID
    user = db.query(User).filter(User.qq_openid == openid).first()
    if not user:
        user = _qq_find_or_create_user(db, openid)
    if not user:
        raise HTTPException(status_code=500, detail="创建用户失败，请重试")
    token = _create_token(user.username)
    return LoginResponse(token=token, user=UserMe(username=user.username))
