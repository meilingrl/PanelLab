"""服务器库：每个用户可管理多台 SSH 服务器。"""
from __future__ import annotations

try:
    from typing import Annotated
except ImportError:
    from typing_extensions import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from config import get_settings
from database import get_db
from models.server import Server
from models.user import User
from routers.auth import get_current_user
from schemas.servers import ServerCreate, ServerItem, ServerListResponse, ServerUpdate
from utils.cipher import encrypt_password


router = APIRouter(prefix="/api/servers", tags=["servers"])


def _fill_from_body(row: Server, body: ServerCreate | ServerUpdate):
    row.name = body.name.strip()
    row.host = body.host.strip()
    row.port = body.port
    row.username = body.username.strip()
    if body.password:
        settings = get_settings()
        row.password_encrypted = encrypt_password(settings.secret_key, body.password)


@router.get("", response_model=ServerListResponse)
def list_servers(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    rows = (
        db.query(Server)
        .filter(Server.user_id == current_user.id)
        .order_by(Server.updated_at.desc())
        .all()
    )
    return ServerListResponse(items=[ServerItem.model_validate(row) for row in rows])


@router.post("", response_model=ServerItem)
def create_server(
    body: ServerCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    row = Server(user_id=current_user.id)
    _fill_from_body(row, body)
    db.add(row)
    db.commit()
    db.refresh(row)
    return ServerItem.model_validate(row)


@router.put("/{server_id}", response_model=ServerItem)
def update_server(
    server_id: int,
    body: ServerUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    row = (
        db.query(Server)
        .filter(Server.id == server_id, Server.user_id == current_user.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="服务器不存在")
    _fill_from_body(row, body)
    db.commit()
    db.refresh(row)
    return ServerItem.model_validate(row)


@router.delete("/{server_id}")
def delete_server(
    server_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    row = (
        db.query(Server)
        .filter(Server.id == server_id, Server.user_id == current_user.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="服务器不存在")
    db.delete(row)
    db.commit()
    return {"message": "服务器已删除"}

