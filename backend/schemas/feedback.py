"""反馈 API 的请求/响应模型。"""
from pydantic import BaseModel, Field


class FeedbackCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000, description="反馈内容")
    contact: str | None = Field(None, max_length=255, description="联系方式（选填）")
