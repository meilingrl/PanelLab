"""Owner-scoped trusted remote workspace endpoints."""
from typing import Annotated
from typing import Any
from fastapi import APIRouter, Body, Depends, Query
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


@router.get('/servers/{server_id}/private-key')
def private_key_summary(server_id: int, db: DB, user: Owner):
    from services import remote_keys
    return remote_keys.get_summary(db, user, server_id)


@router.put('/servers/{server_id}/private-key')
def set_private_key(server_id: int, db: DB, user: Owner, body: Any = Body(None)):
    # Accept any JSON so request validation can never echo key material back.
    from services import remote_keys
    if not isinstance(body, dict):
        remote_keys.invalid('private_key_invalid', 'Send a JSON object with private_key and optional passphrase')
    return remote_keys.save(db, user, server_id, body.get('private_key'), body.get('passphrase'))


@router.delete('/servers/{server_id}/private-key')
def delete_private_key(server_id: int, db: DB, user: Owner):
    from services import remote_keys
    return remote_keys.delete(db, user, server_id)


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


@router.get('/servers/{server_id}/shell-history')
def shell_history(server_id: int, db: DB, user: Owner):
    from services import remote_files
    from services.shell_history import read_shell_history
    with connection.open_remote(db, user, server_id) as client:
        with remote_files.open_sftp(client) as sftp:
            result = read_shell_history(sftp)
    return {**result, 'observed_at': connection.observed_at()}


@router.post('/servers/{server_id}/terminal-ticket')
def terminal_ticket(server_id: int, body: TerminalTicketRequest, db: DB, user: Owner):
    from services.remote_tickets import issue_ticket
    return issue_ticket(db, user, server_id, body.cwd)


@router.get('/remote/tailscale')
def tailscale(user: Annotated[User, Depends(require_operator)]):
    from services.remote_discovery import discover_tailscale
    return discover_tailscale()
