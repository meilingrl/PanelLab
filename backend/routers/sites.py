"""站点配置：CRUD + Nginx 应用。"""
from __future__ import annotations

try:
    from typing import Annotated
except ImportError:
    from typing_extensions import Annotated
from typing import Optional, Union

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import get_db
from models.site_config import SiteConfig
from models.user import User
from routers.auth import get_current_user
from schemas.sites import SiteCreate, SiteItem, SiteListResponse, SiteUpdate
from services.nginx_manager import NginxManager, NginxManagerError, SiteRuntimeConfig

router = APIRouter(prefix="/api/sites", tags=["sites"])
nginx_manager = NginxManager()


def _as_runtime(site: SiteConfig) -> SiteRuntimeConfig:
    return SiteRuntimeConfig(
        id=site.id,
        name=site.name,
        domain=site.domain,
        site_type=site.site_type,
        root_path=site.root_path,
        proxy_target=site.proxy_target,
        listen_port=site.listen_port,
        enabled=site.enabled,
        config_filename=site.config_filename,
    )


def _ensure_unique_fields(
    db: Session,
    body: Union[SiteCreate, SiteUpdate],
    user_id: int,
    current_id: Optional[int] = None,
):
    name = body.name.strip()
    domain = body.domain.strip().lower()
    q1 = db.query(SiteConfig).filter(SiteConfig.user_id == user_id, SiteConfig.name == name)
    q2 = db.query(SiteConfig).filter(SiteConfig.user_id == user_id, SiteConfig.domain == domain)
    if current_id is not None:
        q1 = q1.filter(SiteConfig.id != current_id)
        q2 = q2.filter(SiteConfig.id != current_id)
    if q1.first():
        raise HTTPException(status_code=409, detail="站点名称已存在")
    if q2.first():
        raise HTTPException(status_code=409, detail="域名已存在")


def _fill_from_body(site: SiteConfig, body: Union[SiteCreate, SiteUpdate]):
    site.name = body.name.strip()
    site.domain = body.domain.strip().lower()
    site.site_type = body.site_type
    site.listen_port = body.listen_port
    site.enabled = body.enabled
    if body.site_type == "static":
        site.root_path = body.root_path.strip()
        site.proxy_target = None
    else:
        site.root_path = None
        site.proxy_target = body.proxy_target.strip()


def _apply_site_or_raise(db: Session, site: SiteConfig):
    if not site.config_filename:
        site.config_filename = nginx_manager.get_config_filename(_as_runtime(site))
        db.commit()
        db.refresh(site)

    try:
        nginx_manager.apply_site(_as_runtime(site))
        site.status = "active" if site.enabled else "draft"
        db.commit()
        db.refresh(site)
    except NginxManagerError as exc:
        site.status = "error"
        db.commit()
        db.refresh(site)
        raise HTTPException(status_code=503, detail=exc.detail)


@router.get("", response_model=SiteListResponse)
def list_sites(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    rows = (
        db.query(SiteConfig)
        .filter(SiteConfig.user_id == current_user.id)
        .order_by(SiteConfig.updated_at.desc())
        .all()
    )
    return SiteListResponse(items=[SiteItem.model_validate(row) for row in rows])


@router.get("/{site_id}", response_model=SiteItem)
def get_site(
    site_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    row = (
        db.query(SiteConfig)
        .filter(SiteConfig.id == site_id, SiteConfig.user_id == current_user.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="站点不存在")
    return SiteItem.model_validate(row)


@router.post("", response_model=SiteItem)
def create_site(
    body: SiteCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    _ensure_unique_fields(db, body, current_user.id)
    row = SiteConfig(user_id=current_user.id, status="draft")
    _fill_from_body(row, body)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="站点名称或域名冲突")
    db.refresh(row)
    _apply_site_or_raise(db, row)
    return SiteItem.model_validate(row)


@router.put("/{site_id}", response_model=SiteItem)
def update_site(
    site_id: int,
    body: SiteUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    row = (
        db.query(SiteConfig)
        .filter(SiteConfig.id == site_id, SiteConfig.user_id == current_user.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="站点不存在")
    _ensure_unique_fields(db, body, current_user.id, current_id=site_id)
    _fill_from_body(row, body)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="站点名称或域名冲突")
    db.refresh(row)
    _apply_site_or_raise(db, row)
    return SiteItem.model_validate(row)


@router.delete("/{site_id}")
def delete_site(
    site_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    row = (
        db.query(SiteConfig)
        .filter(SiteConfig.id == site_id, SiteConfig.user_id == current_user.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="站点不存在")
    try:
        nginx_manager.remove_site(_as_runtime(row))
    except NginxManagerError as exc:
        row.status = "error"
        db.commit()
        raise HTTPException(status_code=503, detail=exc.detail)
    db.delete(row)
    db.commit()
    return {"message": "站点已删除"}


@router.post("/{site_id}/apply", response_model=SiteItem)
def apply_site(
    site_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    row = (
        db.query(SiteConfig)
        .filter(SiteConfig.id == site_id, SiteConfig.user_id == current_user.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="站点不存在")
    _apply_site_or_raise(db, row)
    return SiteItem.model_validate(row)
