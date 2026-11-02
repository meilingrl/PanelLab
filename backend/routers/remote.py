"""Owner-scoped trusted remote workspace endpoints."""
from typing import Annotated
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from database import get_db
from models.user import User
from routers.auth import get_current_user, require_operator
from schemas.remote import ProbeRequest, TrustRequest, TerminalTicketRequest
from services import remote_connection as connection

router = APIRouter(prefix='/api', tags=['remote'])
DB = Annotated[Session, Depends(get_db)]
Owner = Annotated[User, Depends(get_current_user)]


@router.post('/servers/{server_id}/connection/probe')
def probe(server_id: int, db: DB, user: Owner, body: ProbeRequest = ProbeRequest()):
    return connection.probe(db, user, server_id, body.auth_mode)


@router.post('/servers/{server_id}/connection/trust')
def trust(server_id: int, body: TrustRequest, db: DB, user: Owner):
    return connection.trust(db, user, server_id, body.token)


@router.get('/servers/{server_id}/connection')
def summary(server_id: int, db: DB, user: Owner):
    return connection.connection_summary(db, user, server_id)


@router.post('/servers/{server_id}/connection/check')
def check(server_id: int, db: DB, user: Owner):
    with connection.open_remote(db, user, server_id) as client:
        from services.remote_files import open_sftp, safe_path
        with open_sftp(client) as sftp:
            home = safe_path(sftp)
    return {'ok': True, 'home': home, 'observed_at': connection.observed_at()}


@router.get('/servers/{server_id}/snapshot')
def snapshot(server_id: int, db: DB, user: Owner, pid: int | None = Query(None, ge=1, le=2147483647), query: str | None = Query(None, max_length=128)):
    from services.remote_probe import collect_snapshot
    with connection.open_remote(db, user, server_id) as client:
        return collect_snapshot(client, pid=pid, query=query)


@router.get('/servers/{server_id}/files')
def files(server_id: int, db: DB, user: Owner, path: str | None = None):
    from services.remote_files import open_sftp, list_directory
    with connection.open_remote(db, user, server_id) as client:
        with open_sftp(client) as sftp:
            return list_directory(sftp, path)


@router.post('/servers/{server_id}/terminal-ticket')
def terminal_ticket(server_id: int, body: TerminalTicketRequest, db: DB, user: Owner):
    from services.remote_tickets import issue_ticket
    return issue_ticket(db, user, server_id, body.cwd)


@router.get('/remote/tailscale')
def tailscale(user: Annotated[User, Depends(require_operator)]):
    from services.remote_discovery import discover_tailscale
    return discover_tailscale()
