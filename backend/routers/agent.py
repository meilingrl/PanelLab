"""Owner-scoped routes to the server Agent. Bodies are accepted as any JSON so that
request validation can never echo secret values back; the Agent validates the details."""
import re
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Path, Query
from sqlalchemy.orm import Session

from database import get_db
from models.user import User
from routers.auth import get_current_user
from services import agent_client
from services.remote_connection import fail

router = APIRouter(prefix='/api/servers/{server_id}/agent', tags=['agent'])
DB = Annotated[Session, Depends(get_db)]
Owner = Annotated[User, Depends(get_current_user)]
JOB_ID_RE = re.compile(r'^[a-z0-9][a-z0-9-]{0,47}$')
RunId = Annotated[int, Path(ge=1, le=2**53 - 1)]


def job_id_param(job_id: str) -> str:
    if not JOB_ID_RE.fullmatch(job_id):
        fail('invalid_job_id', '作业 ID 只能包含小写字母、数字和连字符，且不超过 48 个字符', 422)
    return job_id


JobId = Annotated[str, Depends(job_id_param)]


def invalid_body(message='请求体格式不正确'):
    fail('invalid_body', message, 422)


def object_body(body):
    if not isinstance(body, dict):
        invalid_body('请求体必须是 JSON 对象')
    return body


def with_secrets(args, body):
    secrets = body.get('secret_env')
    if secrets is not None:
        if not isinstance(secrets, dict):
            invalid_body('secret_env 必须是对象')
        args['secret_env'] = secrets
    return args


@router.get('')
def status(server_id: int, db: DB, user: Owner):
    return agent_client.call(db, user, server_id, 'status')


@router.get('/jobs')
def list_jobs(server_id: int, db: DB, user: Owner):
    return agent_client.call(db, user, server_id, 'jobs.list')


@router.post('/jobs')
def create_job(server_id: int, db: DB, user: Owner, body: Any = Body(None)):
    body = object_body(body)
    if not isinstance(body.get('job'), dict):
        invalid_body('job 必须是对象')
    args = with_secrets({'job': body['job']}, body)
    return agent_client.call(db, user, server_id, 'jobs.create', args)


@router.get('/jobs/{job_id}')
def get_job(server_id: int, job_id: JobId, db: DB, user: Owner):
    return agent_client.call(db, user, server_id, 'jobs.get', {'id': job_id})


@router.put('/jobs/{job_id}')
def update_job(server_id: int, job_id: JobId, db: DB, user: Owner, body: Any = Body(None)):
    body = object_body(body)
    revision = body.get('revision')
    if type(revision) is not int:
        invalid_body('revision 必须是整数')
    if not isinstance(body.get('changes'), dict):
        invalid_body('changes 必须是对象')
    args = with_secrets({'id': job_id, 'revision': revision, 'changes': body['changes']}, body)
    return agent_client.call(db, user, server_id, 'jobs.update', args)


@router.delete('/jobs/{job_id}')
def delete_job(server_id: int, job_id: JobId, db: DB, user: Owner):
    return agent_client.call(db, user, server_id, 'jobs.delete', {'id': job_id})


@router.post('/jobs/{job_id}/run')
def run_job(server_id: int, job_id: JobId, db: DB, user: Owner):
    return agent_client.call(db, user, server_id, 'jobs.run_now', {'id': job_id})


@router.post('/jobs/{job_id}/pause')
def pause_job(server_id: int, job_id: JobId, db: DB, user: Owner):
    return agent_client.call(db, user, server_id, 'jobs.pause', {'id': job_id})


@router.post('/jobs/{job_id}/resume')
def resume_job(server_id: int, job_id: JobId, db: DB, user: Owner):
    return agent_client.call(db, user, server_id, 'jobs.resume', {'id': job_id})


@router.get('/jobs/{job_id}/runs')
def list_runs(server_id: int, job_id: JobId, db: DB, user: Owner,
              status: str | None = Query(None, max_length=32), before: int | None = Query(None, ge=1, le=2**53 - 1),
              limit: int | None = Query(None, ge=1, le=200)):
    args = {'job_id': job_id}
    for key, value in (('status', status), ('before', before), ('limit', limit)):
        if value is not None:
            args[key] = value
    return agent_client.call(db, user, server_id, 'runs.list', args)


@router.get('/runs/{run_id}/output')
def run_output(server_id: int, run_id: RunId, db: DB, user: Owner,
               offset: int = Query(0, ge=0, le=2**53 - 1), limit: int | None = Query(None, ge=1, le=262144)):
    args = {'run_id': run_id, 'offset': offset}
    if limit is not None:
        args['limit'] = limit
    return agent_client.call(db, user, server_id, 'runs.output', args)


@router.post('/runs/{run_id}/cancel')
def cancel_run(server_id: int, run_id: RunId, db: DB, user: Owner):
    return agent_client.call(db, user, server_id, 'runs.cancel', {'run_id': run_id})


@router.post('/schedule-preview')
def schedule_preview(server_id: int, db: DB, user: Owner, body: Any = Body(None)):
    body = object_body(body)
    if not isinstance(body.get('schedule'), dict):
        invalid_body('schedule 必须是对象')
    return agent_client.call(db, user, server_id, 'jobs.preview', {'schedule': body['schedule']})


@router.get('/alerts')
def get_alerts(server_id: int, db: DB, user: Owner):
    return agent_client.call(db, user, server_id, 'alerts.get')


@router.put('/alerts')
def configure_alerts(server_id: int, db: DB, user: Owner, body: Any = Body(None)):
    return agent_client.call(db, user, server_id, 'alerts.configure', object_body(body))


@router.post('/alerts/test')
def test_alert_email(server_id: int, db: DB, user: Owner):
    return agent_client.call(db, user, server_id, 'alerts.test_email')


@router.post('/install')
def install(server_id: int, db: DB, user: Owner):
    return agent_client.upload_bundle(db, user, server_id)
