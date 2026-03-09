"""站点配置请求/响应模型。"""
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


SiteType = Literal["static", "proxy"]


class SiteBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    domain: str = Field(..., min_length=1, max_length=255)
    site_type: SiteType
    root_path: str = Field(default="", max_length=512)
    proxy_target: str = Field(default="", max_length=512)
    listen_port: int = Field(default=80, ge=1, le=65535)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_by_type(self):
        if self.site_type == "static":
            if not self.root_path or not self.root_path.strip():
                raise ValueError("静态站点必须填写根目录")
        if self.site_type == "proxy":
            if not self.proxy_target or not self.proxy_target.strip():
                raise ValueError("反向代理站点必须填写代理目标地址")
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

