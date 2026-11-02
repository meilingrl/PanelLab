"""WebSocket shell with a single-use ticket and the shared pinned transport."""
import asyncio
import json
import shlex
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from services.remote_connection import open_remote, remote_deadline
from services.remote_files import open_sftp, safe_path
from services.remote_tickets import consume_ticket

router = APIRouter(prefix='/api/ws', tags=['terminal'])


@router.websocket('/terminal')
async def terminal_ws(websocket: WebSocket, ticket: str = Query(..., max_length=128),
                      server_id: int = Query(...), db: Session = Depends(get_db)):
    await websocket.accept()
    context = None
    channel = None
    tasks = []
    try:
        user, server_id, cwd = consume_ticket(db, ticket, server_id)
        context = open_remote(db, user, server_id)
        client = await asyncio.to_thread(context.__enter__)

        def start_shell():
            resolved = None
            if cwd is not None:
                with open_sftp(client) as sftp:
                    resolved = safe_path(sftp, cwd)
            with remote_deadline(client):
                shell = client.invoke_shell(term='xterm')
                try:
                    shell.settimeout(10)
                    if resolved is not None:
                        shell.sendall('cd -- ' + shlex.quote(resolved) + '\r')
                    return shell
                except Exception:
                    shell.close()
                    raise

        channel = await asyncio.to_thread(start_shell)
        await websocket.send_text(json.dumps({'type': 'panellab-terminal-ready', 'version': 1}, separators=(',', ':')))
        await websocket.send_text('Connected to trusted SSH server.\r\n')

        async def ws_to_ssh():
            while True:
                data = await websocket.receive_text()
                if data.startswith('{"type":"resize"'):
                    try:
                        msg = json.loads(data)
                        cols = max(1, min(500, int(msg.get('cols', 80))))
                        rows = max(1, min(200, int(msg.get('rows', 24))))
                        await asyncio.to_thread(channel.resize_pty, width=cols, height=rows)
                    except (ValueError, TypeError, KeyError):
                        pass
                else:
                    # Complete writes off-loop; PTY remains interactive and user controlled.
                    await asyncio.to_thread(channel.sendall, data)

        async def ssh_to_ws():
            while True:
                if channel.recv_ready():
                    await websocket.send_text(channel.recv(4096).decode('utf-8', errors='replace'))
                elif channel.exit_status_ready():
                    await websocket.send_text('\r\nSession closed.\r\n')
                    return
                else:
                    await asyncio.sleep(0.01)

        tasks = [asyncio.create_task(ws_to_ssh()), asyncio.create_task(ssh_to_ws())]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except HTTPException as exc:
        message = exc.detail.get('message', 'Remote connection failed') if isinstance(exc.detail, dict) else 'Remote connection failed'
        await websocket.send_text(message + '\r\n')
    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await websocket.send_text('SSH session failed. Check connection and open a new terminal.\r\n')
        except Exception:
            pass
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if channel is not None:
            channel.close()
        if context is not None:
            await asyncio.to_thread(context.__exit__, None, None, None)
        try:
            await websocket.close()
        except RuntimeError:
            pass
