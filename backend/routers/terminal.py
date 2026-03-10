import asyncio
import json
from typing import Optional

try:
    from typing import Annotated
except ImportError:
    from typing_extensions import Annotated

import paramiko
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, Query
from sqlalchemy.orm import Session

from config import get_settings
from database import get_db
from models.server import Server
from models.user import User
from routers.auth import _decode_token
from utils.cipher import decrypt_password

router = APIRouter(prefix="/api/ws", tags=["terminal"])

async def get_ws_current_user(token: str, db: Session) -> Optional[User]:
    username = _decode_token(token)
    if not username:
        return None
    user = db.query(User).filter(User.username == username).first()
    return user

@router.websocket("/terminal")
async def terminal_ws(
    websocket: WebSocket,
    token: str = Query(...),
    server_id: int = Query(..., description="服务器库中的服务器 ID"),
    db: Session = Depends(get_db)
):
    await websocket.accept()

    user = await get_ws_current_user(token, db)
    if not user:
        await websocket.send_text("Authentication failed. Please login.\r\n")
        await websocket.close(code=1008)
        return

    row = db.query(Server).filter(Server.id == server_id, Server.user_id == user.id).first()
    if not row:
        await websocket.send_text("Server not found or access denied. Please add the server in User Center -> Server Data.\r\n")
        await websocket.close(code=1008)
        return

    settings = get_settings()
    password = decrypt_password(settings.secret_key, row.password_encrypted) if row.password_encrypted else None
    if not password:
        await websocket.send_text("Failed to decrypt password. Please set password for this server in User Center.\r\n")
        await websocket.close(code=1008)
        return

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    def _do_connect():
        client.connect(
            hostname=row.host,
            port=row.port,
            username=row.username,
            password=password,
            timeout=15,
            allow_agent=False,
            look_for_keys=False,
        )

    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _do_connect)
    except Exception as e:
        await websocket.send_text(f"SSH Connection failed: {e}\r\n")
        await websocket.close(code=1008)
        return

    try:
        channel = client.invoke_shell(term="xterm")
        channel.setblocking(False)
    except Exception as e:
        await websocket.send_text(f"Failed to open shell: {e}\r\n")
        client.close()
        await websocket.close(code=1008)
        return

    await websocket.send_text(f"Connected to {row.username}@{row.host}:{row.port}\r\n")

    async def ws_to_ssh():
        try:
            while True:
                data = await websocket.receive_text()
                if data.startswith('{"type":"resize"'):
                    try:
                        msg = json.loads(data)
                        cols = msg.get("cols", 80)
                        rows = msg.get("rows", 24)
                        channel.resize_pty(width=cols, height=rows)
                    except Exception:
                        pass
                else:
                    channel.send(data)
        except asyncio.CancelledError:
            pass
        except WebSocketDisconnect:
            pass
        except Exception as e:
            print("WS read error:", e)

    async def ssh_to_ws():
        try:
            while not channel.exit_status_ready():
                if channel.recv_ready():
                    data = channel.recv(4096)
                    try:
                        await websocket.send_text(data.decode('utf-8', errors='replace'))
                    except Exception:
                        pass
                else:
                    await asyncio.sleep(0.01)
            # send remaining output
            while channel.recv_ready():
                data = channel.recv(4096)
                try:
                    await websocket.send_text(data.decode('utf-8', errors='replace'))
                except Exception:
                    pass
            await websocket.send_text("\r\nSession closed.\r\n")
            await websocket.close()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print("SSH read error:", e)
            try:
                await websocket.close()
            except Exception:
                pass

    try:
        await asyncio.gather(ws_to_ssh(), ssh_to_ws())
    except asyncio.CancelledError:
        pass
    finally:
        client.close()