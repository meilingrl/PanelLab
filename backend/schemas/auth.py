"""认证相关请求/响应模型。"""
from pydantic import BaseModel, Field


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
    username: str

    class Config:
        from_attributes = True


LoginResponse.model_rebuild()
