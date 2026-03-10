"""文档服务：使用手册等静态文档。"""
import os
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

router = APIRouter(prefix="/api/docs", tags=["docs"])

# 项目根目录：backend/routers/docs.py -> 上两级到项目根
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_USER_MANUAL_PATH = _PROJECT_ROOT / "docs" / "user" / "user-manual.md"


@router.get("/user-manual", response_class=PlainTextResponse)
def get_user_manual():
    """返回用户使用说明书 Markdown 原文。"""
    if not _USER_MANUAL_PATH.is_file():
        return PlainTextResponse(content="# 使用手册\n\n文档暂未就绪。", status_code=200)
    text = _USER_MANUAL_PATH.read_text(encoding="utf-8")
    return PlainTextResponse(content=text)
