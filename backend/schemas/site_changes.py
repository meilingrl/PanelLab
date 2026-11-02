from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator
from schemas.sites import SiteBase, SiteItem

Operation = Literal['create','update','apply','delete']
class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    operation: Operation
    site_id: Optional[int] = Field(default=None, gt=0)
    config: Optional[SiteBase] = None

    @model_validator(mode='after')
    def validate_operation(self):
        if (self.operation == 'create') != (self.site_id is None):
            raise ValueError('create 不接受 site_id；其他操作必须提供 site_id')
        if (self.operation in ('create','update')) != (self.config is not None):
            raise ValueError('仅 create/update 必须提供 config')
        return self

class PreviewClaims(PreviewRequest):
    purpose: Literal['site-change-preview']
    aud: Literal['site-change']
    sub: str
    jti: str = Field(pattern=r'^[a-f0-9]{32}$')
    iat: int
    exp: int
    database_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    directory_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    runtime_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    filename: str = Field(pattern=r'^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,200}\.conf$')

class ExecuteRequest(BaseModel):
    model_config = ConfigDict(extra='forbid',strict=True)
    token: str = Field(min_length=1,max_length=16000)

class PreviewResponse(BaseModel):
    token: str
    expires_at: datetime
    operation: Operation
    site_id: Optional[int]
    site_name: str
    before: Optional[str]
    after: Optional[str]
    diff: str
    simulated: bool
    warnings: list[str]

class ChangeStep(BaseModel):
    name: str
    status: Literal['succeeded','failed']
    message: str

class SiteChangeItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    site_id: Optional[int]
    site_name: str
    operation: Operation
    status: Literal['pending','applied','simulated','failed_restored','recovery_failed']
    created_at: datetime
    before: Optional[str]
    after: Optional[str]
    steps: list[ChangeStep]
    message: str
    simulated: bool

class ChangeListResponse(BaseModel):
    items: list[SiteChangeItem]

class ExecuteResponse(BaseModel):
    change: SiteChangeItem
    site: Optional[SiteItem]
