"""服务器库（Server）请求/响应模型。"""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ServerBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    host: str = Field(..., min_length=1, max_length=255)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(..., min_length=1, max_length=64)
    password: Optional[str] = Field(default=None, max_length=255, description="明文密码，仅用于创建/更新时加密存储")


class ServerCreate(ServerBase):
    pass


class ServerUpdate(ServerBase):
    pass


class ServerItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    host: str
    port: int
    username: str
    created_at: datetime
    updated_at: datetime


class ServerListResponse(BaseModel):
    items: List[ServerItem]

