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


class SmsSendRequest(BaseModel):
    phone: str = Field(..., min_length=11, max_length=11, pattern=r"^1\d{10}$")


class SmsLoginRequest(BaseModel):
    phone: str = Field(..., min_length=11, max_length=11, pattern=r"^1\d{10}$")
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


LoginResponse.model_rebuild()
