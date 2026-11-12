"""WebSocket shell with a single-use ticket and a dedicated pinned transport."""
import asyncio
import json
import shlex
import secrets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from services.remote_connection import open_remote, remote_deadline
from services.remote_files import open_sftp, safe_path
from services.remote_tickets import consume_ticket

router = APIRouter(prefix='/api/ws', tags=['terminal'])


TERMINAL_STARTUP_LIMIT = 64 * 1024


def establish_shell_directory(shell, directory):
    """Verify cwd in this PTY, consuming bounded setup/echo output before ready.

    The random acknowledgement has control-byte delimiters that never occur in
    the typed command. Echoed commands or unrelated remote text cannot match it.
    The caller keeps the write and all reads inside the setup deadline.
    """
    nonce = secrets.token_hex(16)
    quoted = shlex.quote(directory)
    shell.sendall(
        f'if cd -- {quoted} && [ "$(command pwd -P)" = {quoted} ]; '
        f"then printf '\\036PANEL_CWD_%s:OK\\037' {nonce}; "
        f"else printf '\\036PANEL_CWD_%s:FAIL\\037' {nonce}; fi\r"
    )
    prefix = b'\x1ePANEL_CWD_' + nonce.encode('ascii')
    success, failure = prefix + b':OK\x1f', prefix + b':FAIL\x1f'
    output = bytearray()
    while True:
        if len(output) > TERMINAL_STARTUP_LIMIT:
            raise HTTPException(503, detail={'code': 'terminal_startup_limit', 'message': 'Terminal startup output exceeded limit.'})
        if failure in output:
            raise HTTPException(503, detail={'code': 'terminal_cwd_failed', 'message': 'Unable to establish requested terminal directory.'})
        index = output.find(success)
        if index >= 0:
            # Preserve normal PTY output after acknowledgement, such as the prompt.
            return bytes(output[index + len(success):])
        chunk = shell.recv(min(4096, TERMINAL_STARTUP_LIMIT + 1 - len(output)))
        if not chunk:
            raise HTTPException(503, detail={'code': 'terminal_cwd_failed', 'message': 'Unable to establish requested terminal directory.'})
        output.extend(chunk)


@router.websocket('/terminal')
async def terminal_ws(websocket: WebSocket, ticket: str = Query(..., max_length=128),
                      server_id: int = Query(...), db: Session = Depends(get_db)):
    await websocket.accept()
    context = None
    channel = None
    tasks = []
    try:
        user, server_id, cwd = consume_ticket(db, ticket, server_id)
        # The PTY keeps its own connection for its whole life; it never borrows a pooled one.
        context = open_remote(db, user, server_id, dedicated=True)
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
                    initial_output = establish_shell_directory(shell, resolved) if resolved is not None else b''
                    return shell, initial_output
                except Exception:
                    shell.close()
                    raise

        channel, initial_output = await asyncio.to_thread(start_shell)
        await websocket.send_text(json.dumps({'type': 'panellab-terminal-ready', 'version': 1}, separators=(',', ':')))
        await websocket.send_text('Connected to trusted SSH server.\r\n')
        if initial_output:
            await websocket.send_text(initial_output.decode('utf-8', errors='replace'))

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
