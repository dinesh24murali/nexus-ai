"""Account deletion (TASK-028, FS-008): the request side.

Asking to delete an account is one transaction: the user becomes `pending_deletion` (they can no
longer sign in or use a token), every session is revoked, work that has not started is cancelled,
and a `purge_user` job is queued. The erasure itself is that job (`jobs/handlers.py::purge_user`),
which runs within minutes and always inside the 24 h the user was promised. Asking twice queues
one purge (the idempotency key is the user id).
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import ApiError
from app.db.models import User
from app.db.repositories import refresh_tokens, users
from app.jobs import queue

PURGE_JOB = "purge_user"
PURGE_ATTEMPTS = 8  # with the queue's back-off this rides out a few minutes of an outage

Factory = async_sessionmaker[AsyncSession]


def _mismatch() -> ApiError:
    return ApiError(
        400, "CONFIRMATION_MISMATCH", "The email you typed does not match this account."
    )


async def request_deletion(factory: Factory, user: User, *, confirm_email: str) -> None:
    # The column is case-insensitive (citext), so the confirmation is too.
    if confirm_email.strip().casefold() != user.email.casefold():
        raise _mismatch()
    async with factory() as session, session.begin():
        await users.set_status(session, user.id, "pending_deletion")
        await refresh_tokens.revoke_all_for_user(session, user.id, datetime.now(UTC))
        await queue.cancel_all_queued_for_user(session, user.id, keep=PURGE_JOB)
        await queue.enqueue(
            session,
            PURGE_JOB,
            {"user_id": str(user.id)},
            f"purge:{user.id}",
            user_id=user.id,
            max_attempts=PURGE_ATTEMPTS,
        )


def purge_payload_user(user_id: uuid.UUID | None, payload: dict[str, object]) -> uuid.UUID:
    """Whose purge this is. The job row's `user_id` becomes NULL once the user row is gone (FK
    `SET NULL`), so a retry after a crash reads the id from the payload."""
    if user_id is not None:
        return user_id
    return uuid.UUID(str(payload["user_id"]))
