"""Metadata-only mutations and read-only remote workload endpoints."""
import posixpath
from typing import Annotated
from urllib.parse import quote
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session
from database import get_db
from models.user import User
from models.workload import Workload, ReportedRun
from routers.auth import get_current_user
from schemas.workloads import WorkloadCreate, WorkloadPatch, RunCreate
from services import remote_connection, workloads as service

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


@router.get('/{workload_id}/files')
def files(workload_id: int, db: DB, user: Owner, path: str | None = None):
    return service.files(db,user,service.owned(db,user,workload_id),path)


def text_preview(db, user, row, path):
    data, metadata = service.read_file(db, user, row, path)
    return {**metadata, 'text': data.decode('utf-8', errors='replace')}


@router.get('/{workload_id}/log')
def bound_log(workload_id: int, db: DB, user: Owner):
    row = service.owned(db, user, workload_id)
    if not row.log_path:
        raise HTTPException(409, detail={'code': 'log_unbound', 'message': 'No log file is bound to this task'})
    server = remote_connection.owned_server(db, user, row.server_id)
    preview = text_preview(db, user, row, row.log_path)
    return {**preview, 'context': service.binding_context(row, server)}


@router.get('/{workload_id}/text')
def text(workload_id: int, db: DB, user: Owner, path: str):
    return text_preview(db, user, service.owned(db, user, workload_id), path)


@router.get('/{workload_id}/download')
def download(workload_id: int, db: DB, user: Owner, path: str):
    row = service.owned(db,user,workload_id)
    data, metadata = service.read_file(db,user,row,path,download=True)
    filename = quote(posixpath.basename(metadata['path']),safe='')
    return Response(data,media_type='application/octet-stream',headers={
        'Content-Disposition':"attachment; filename*=UTF-8''"+filename,
        'X-File-Changed-During-Read':str(metadata['changed_during_read']).lower(),
        'X-File-Size':str(metadata['size'])})


@router.get('/{workload_id}/runs')
def runs(workload_id: int, db: DB, user: Owner, before_id: int | None = Query(None,gt=0), limit: int = Query(20,ge=1,le=50)):
    service.owned(db,user,workload_id)
    query = db.query(ReportedRun).filter(ReportedRun.workload_id==workload_id)
    if before_id is not None: query = query.filter(ReportedRun.id<before_id)
    rows = query.order_by(ReportedRun.id.desc()).limit(limit).all()
    return {'items':[service.serialize(r) for r in rows]}


@router.post('/{workload_id}/runs')
def report(workload_id: int, body: RunCreate, db: DB, user: Owner):
    row = service.owned(db,user,workload_id)
    if body.artifact_path is not None: service.contained(body.artifact_path,row.root_path)
    values = body.model_dump()
    # MySQL and SQLite store UTC without a timezone; serialize explicitly as UTC.
    for key in ('started_at','finished_at'):
        if values[key] is not None: values[key] = values[key].replace(tzinfo=None)
    run = ReportedRun(workload_id=row.id,reported_by=user.id,source='reported',**values)
    db.add(run)
    db.commit()
    db.refresh(run)
    return service.serialize(run)
