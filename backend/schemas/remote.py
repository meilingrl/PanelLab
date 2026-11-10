from typing import Any, Literal
from pydantic import BaseModel, Field


class ProbeRequest(BaseModel):
    auth_mode: Literal['password', 'key', 'tailscale'] = 'password'


class TrustRequest(BaseModel):
    token: str = Field(min_length=1, max_length=16384)


class TerminalTicketRequest(BaseModel):
    cwd: str | None = Field(default=None, max_length=4096)


class PrivateKeyRequest(BaseModel):
    # Types and lengths are checked by the service so validation errors never echo key material.
    private_key: Any = ''
    passphrase: Any = None
