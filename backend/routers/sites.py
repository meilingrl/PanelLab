"""站点配置：CRUD + Nginx 应用。"""
from __future__ import annotations

try:
    from typing import Annotated
except ImportError:
    from typing_extensions import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models.site_config import SiteConfig
from models.user import User
from routers.auth import require_operator
from schemas.sites import SiteItem, SiteListResponse

router = APIRouter(prefix="/api/sites", tags=["sites"])


@router.get("", response_model=SiteListResponse)
def list_sites(
    current_user: Annotated[User, Depends(require_operator)],
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
    current_user: Annotated[User, Depends(require_operator)],
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


def _preview_required():
    raise HTTPException(status_code=428, detail="请先调用 /api/site-changes/preview，再确认执行 /api/site-changes/execute")


@router.post("")
def create_site(current_user: Annotated[User, Depends(require_operator)]):
    _preview_required()


@router.put("/{site_id}")
def update_site(site_id: int, current_user: Annotated[User, Depends(require_operator)]):
    _preview_required()


@router.delete("/{site_id}")
def delete_site(site_id: int, current_user: Annotated[User, Depends(require_operator)]):
    _preview_required()


@router.post("/{site_id}/apply")
def apply_site(site_id: int, current_user: Annotated[User, Depends(require_operator)]):
    _preview_required()
