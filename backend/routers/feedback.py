"""用户反馈：提交反馈并落库。"""
from __future__ import annotations

try:
    from typing import Annotated
except ImportError:
    from typing_extensions import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import get_db
from models.feedback import Feedback
from models.user import User
from routers.auth import get_current_user
from schemas.feedback import FeedbackCreate

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


@router.post("")
def submit_feedback(
    body: FeedbackCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """提交反馈，需登录。内容与可选联系方式落库。"""
    row = Feedback(
        user_id=current_user.id,
        content=body.content.strip(),
        contact=body.contact.strip() if body.contact else None,
    )
    db.add(row)
    db.commit()
    return {"message": "感谢您的反馈，我们已收到。"}
