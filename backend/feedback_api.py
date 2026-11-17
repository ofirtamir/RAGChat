"""Answer feedback from chat users (/api/questions/{id}/feedback).

The asker rates their own answer 👍/👎 with an optional comment; the admin sees
it in the question log (src/chat_log).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src import chat_log
from .auth import get_active_user_id

router = APIRouter(prefix="/api")


class FeedbackRequest(BaseModel):
    rating: int  # 1 = helpful, -1 = not helpful, 0 = withdraw
    comment: str | None = Field(default=None, max_length=2000)


@router.post("/questions/{question_id}/feedback")
def question_feedback(question_id: str, request: FeedbackRequest,
                      user_id: str = Depends(get_active_user_id)):
    if request.rating not in (-1, 0, 1):
        raise HTTPException(status_code=400, detail="rating must be -1, 0 or 1")
    if not chat_log.set_feedback(question_id, user_id, request.rating, request.comment):
        raise HTTPException(status_code=404, detail="Question not found")
    return {"status": "ok"}
