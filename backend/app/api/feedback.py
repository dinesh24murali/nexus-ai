"""Rate an assistant message (`PUT /messages/{messageId}/feedback`)."""

import uuid

from fastapi import APIRouter

from app.api.deps import CurrentUserDep, FeedbackDep
from app.api.schemas import FeedbackOut, FeedbackRequest
from app.core.errors import ApiError

router = APIRouter(tags=["feedback"])


@router.put(
    "/messages/{message_id}/feedback",
    response_model=FeedbackOut,
    summary="Rate an assistant message (overwrites a previous rating)",
)
async def put_feedback(
    message_id: str, body: FeedbackRequest, current: CurrentUserDep, service: FeedbackDep
) -> FeedbackOut:
    try:
        mid = uuid.UUID(message_id)
    except ValueError:  # a malformed id is just another unknown id (404), as the spec documents
        raise ApiError(404, "NOT_FOUND", "Message not found.") from None
    row = await service.rate(current.id, mid, score=body.score, comment=body.comment)
    return FeedbackOut(
        message_id=row.message_id, score=row.score, comment=row.comment, created_at=row.created_at
    )
