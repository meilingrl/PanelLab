"""Metadata-only mutations and read-only remote workload endpoints."""
import posixpath
from typing import Annotated
from urllib.parse import quote
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session
from database import get_db
from models.user import User
from models.workload import Workload
from routers.auth import get_current_user
from schemas.workloads import WorkloadCreate, WorkloadPatch
from services import workloads as service

router = APIRouter(prefix='/api/workloads', tags=['workloads'])
DB = Annotated[Session, Depends(get_db)]
Owner = Annotated[User, Depends(get_current_user)]


@router.get('')
def listing(db: DB, user: Owner):
    return {'items':[service.serialize(r) for r in db.query(Workload).filter(Workload.user_id==user.id).order_by(Workload.id.desc()).all()]}


@router.post('')
def create(body: WorkloadCreate, db: DB, user: Owner):
    service.validate_binding(db,user,body)
    row = Workload(user_id=user.id, **body.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return service.serialize(row)


@router.get('/{workload_id}')
def show(workload_id: int, db: DB, user: Owner):
    return service.serialize(service.owned(db,user,workload_id))


@router.patch('/{workload_id}')
def update(workload_id: int, body: WorkloadPatch, db: DB, user: Owner):
    row = service.owned(db,user,workload_id)
    patch = body.model_dump(exclude_unset=True)
    try:
        merged = WorkloadCreate.model_validate({**{k:getattr(row,k) for k in WorkloadCreate.model_fields},**patch})
    except ValidationError:
        raise HTTPException(422, detail={'code':'invalid_workload','message':'Updated workload fields are invalid'}) from None
    if any(getattr(row,k) != getattr(merged,k) for k in service.BINDING_FIELDS):
        service.validate_binding(db,user,merged)
    for key,value in merged.model_dump().items(): setattr(row,key,value)
    db.commit()
    db.refresh(row)
    return service.serialize(row)


@router.delete('/{workload_id}')
def remove(workload_id: int, db: DB, user: Owner):
    row = service.owned(db,user,workload_id)
    db.delete(row)
    db.commit()
    return {'deleted':True,'workload_id':workload_id}


@router.get('/{workload_id}/observation')
def observation(workload_id: int, db: DB, user: Owner):
    return service.observation(db,user,service.owned(db,user,workload_id))
