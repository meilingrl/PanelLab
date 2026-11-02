from typing import Annotated
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from database import get_db
from models.user import User
from models.site_change import SiteChange
from routers.auth import require_operator
from schemas.site_changes import PreviewRequest, PreviewResponse, ExecuteRequest, ExecuteResponse, ChangeListResponse
from services import site_changes
router=APIRouter(prefix='/api/site-changes',tags=['site-changes'])

@router.post('/preview',response_model=PreviewResponse)
def preview(body:PreviewRequest,user:Annotated[User,Depends(require_operator)],db:Annotated[Session,Depends(get_db)]):
    return site_changes.preview(db,user,body)

@router.post('/execute',response_model=ExecuteResponse)
def execute(body:ExecuteRequest,user:Annotated[User,Depends(require_operator)],db:Annotated[Session,Depends(get_db)]):
    return site_changes.execute(db,user,body.token)

@router.get('',response_model=ChangeListResponse)
def history(user:Annotated[User,Depends(require_operator)],db:Annotated[Session,Depends(get_db)],limit:Annotated[int,Query(ge=1,le=50)]=20,before_id:Annotated[int|None,Query(gt=0)]=None):
    query=db.query(SiteChange).filter_by(user_id=user.id)
    if before_id is not None: query=query.filter(SiteChange.id<before_id)
    return dict(items=query.order_by(SiteChange.id.desc()).limit(limit).all())
