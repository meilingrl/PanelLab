from typing import Literal
from pydantic import BaseModel, Field


class ProbeRequest(BaseModel):
    auth_mode: Literal['password', 'tailscale'] = 'password'


class TrustRequest(BaseModel):
    token: str = Field(min_length=1, max_length=16384)


class TerminalTicketRequest(BaseModel):
    cwd: str | None = Field(default=None, max_length=4096)
