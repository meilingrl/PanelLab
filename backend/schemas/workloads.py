"""Strict API inputs; observations never stand in for reported outcomes."""
import posixpath
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def absolute_path(value):
    if value is None: return value
    if not value.startswith('/') or len(value) > 4096 or any(ord(c) < 32 for c in value) or '..' in value.split('/'):
        raise ValueError('Absolute paths without traversal or controls are required')
    return posixpath.normpath(value)


class WorkloadCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=100)
    kind: Literal['training', 'website', 'scheduled']
    server_id: int = Field(gt=0)
    root_path: str
    log_path: str | None = None
    pid: int | None = Field(None, gt=0, le=2147483647)
    process_identity: str | None = Field(None, min_length=1, max_length=128)
    website_url: str | None = Field(None, max_length=2048)
    schedule_note: str | None = Field(None, max_length=200)
    notes: str | None = Field(None, max_length=2000)

    @field_validator('name')
    @classmethod
    def name_not_blank(cls, value):
        if not value.strip(): raise ValueError('Name cannot be blank')
        return value.strip()

    @field_validator('root_path', 'log_path')
    @classmethod
    def path(cls, value): return absolute_path(value)

    @field_validator('website_url')
    @classmethod
    def website(cls, value):
        if value is None: return value
        url = urlsplit(value)
        if url.scheme not in ('http', 'https') or not url.hostname or url.username is not None or url.password is not None or any(c.isspace() for c in value):
            raise ValueError('Website URL must be http(s) without user information')
        try: url.port
        except ValueError: raise ValueError('Invalid website port')
        return value

    @model_validator(mode='after')
    def binding(self):
        if (self.pid is None) != (self.process_identity is None):
            raise ValueError('PID and process_identity must be supplied together')
        return self


class WorkloadPatch(BaseModel):
    # The merged complete object is validated by WorkloadCreate before mutation.
    model_config = ConfigDict(extra='forbid')
    name: str | None = None
    kind: str | None = None
    server_id: int | None = None
    root_path: str | None = None
    log_path: str | None = None
    pid: int | None = None
    process_identity: str | None = None
    website_url: str | None = None
    schedule_note: str | None = None
    notes: str | None = None
