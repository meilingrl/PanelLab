"""认证相关请求/响应模型。"""
from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    password: str = Field(..., min_length=6, max_length=128)


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(..., min_length=6, max_length=128)


class LoginResponse(BaseModel):
    token: str
    user: "UserMe"


class UserMe(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    username: str


LoginResponse.model_rebuild()
