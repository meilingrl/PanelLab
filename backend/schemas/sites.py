"""站点配置请求/响应模型。"""
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator
from services.site_validation import validate_config


SiteType = Literal["static", "proxy"]


class SiteBase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(..., min_length=1, max_length=64)
    domain: str = Field(..., min_length=1, max_length=255)
    site_type: SiteType
    root_path: str = Field(default="", max_length=512)
    proxy_target: str = Field(default="", max_length=512)
    listen_port: int = Field(default=80, ge=1, le=65535)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_by_type(self):
        self.name = self.name.strip()
        if not self.name or any(ord(c) < 32 or ord(c) == 127 for c in self.name):
            raise ValueError("站点名称不能为空或包含控制字符")
        validate_config(self.domain, self.site_type, self.root_path, self.proxy_target, self.listen_port)
        self.domain = self.domain.lower()
        return self


class SiteCreate(SiteBase):
    pass


class SiteUpdate(SiteBase):
    pass


class SiteItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    domain: str
    site_type: SiteType
    root_path: Optional[str] = None
    proxy_target: Optional[str] = None
    listen_port: int
    enabled: bool
    config_filename: Optional[str] = None
    status: str
    updated_at: datetime


class SiteListResponse(BaseModel):
    items: List[SiteItem]

