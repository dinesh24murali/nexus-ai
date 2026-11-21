"""Streaming chat endpoints (ADR-0006): send, regenerate, cancel, and the message history."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Query, Response
from fastapi.responses import StreamingResponse

from app.api import sse
from app.api.deps import CurrentUserDep, FactoryDep, RunsDep
from app.api.schemas import MessageOut, MessagePage, ResumeRunRequest, SendMessageRequest
from app.core.errors import ApiError
from app.db.repositories import conversations, feedback, messages
from app.services.runs import Run

router = APIRouter(prefix="/conversations", tags=["chat"])

_STREAM_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",  # proxies must not buffer the stream (Caddy: flush_interval -1)
}


def _not_found() -> ApiError:
    return ApiError(404, "NOT_FOUND", "Conversation not found.")


def _parse_id(raw: str) -> uuid.UUID:
    """A malformed id is just another unknown id (404), as the spec documents; never a 422."""
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise _not_found() from None


async def _frames(run: Run) -> AsyncIterator[str]:
    """The run's events as SSE frames, with a ping while it is quiet.

    Leaving this generator (the client went away) does not touch the run: it is a separate task
    that keeps going and saves its result.
    """
    seq = 0
    while True:
        try:
            event = await asyncio.wait_for(run.events.get(), timeout=sse.PING_INTERVAL_S)
        except TimeoutError:
            yield sse.PING
            continue
        if event is None:
            return
        seq += 1
        yield sse.frame(event, seq)


def _stream(run: Run) -> StreamingResponse:
    return StreamingResponse(_frames(run), media_type="text/event-stream", headers=_STREAM_HEADERS)


@router.post(
    "/{conversation_id}/messages",
    summary="Send a user message and stream the assistant run",
    response_class=StreamingResponse,
)
async def send_message(
    conversation_id: str, body: SendMessageRequest, current: CurrentUserDep, runs: RunsDep
) -> StreamingResponse:
    cid = _parse_id(conversation_id)
    run = await runs.start_send(current.id, current.user.display_name, cid, body.content)
    return _stream(run)


@router.post(
    "/{conversation_id}/regenerate",
    summary="Regenerate the latest assistant message and stream the new run",
    response_class=StreamingResponse,
)
async def regenerate(
    conversation_id: str, current: CurrentUserDep, runs: RunsDep
) -> StreamingResponse:
    cid = _parse_id(conversation_id)
    run = await runs.start_regenerate(current.id, current.user.display_name, cid)
    return _stream(run)


@router.post(
    "/{conversation_id}/runs/resume",
    summary="Answer a pending tool approval and stream the resumed run",
    response_class=StreamingResponse,
)
async def resume_run(
    conversation_id: str, body: ResumeRunRequest, current: CurrentUserDep, runs: RunsDep
) -> StreamingResponse:
    cid = _parse_id(conversation_id)
    edited = (
        body.edited_args.model_dump(mode="json", exclude_none=True) if body.edited_args else None
    )
    run = await runs.start_resume(
        current.id, current.user.display_name, cid, body.interrupt_id, body.decision, edited
    )
    return _stream(run)


@router.post(
    "/{conversation_id}/runs/cancel",
    status_code=202,
    summary="Cancel the active run; the partial answer is kept with status cancelled",
)
async def cancel_run(
    conversation_id: str, current: CurrentUserDep, factory: FactoryDep, runs: RunsDep
) -> Response:
    cid = _parse_id(conversation_id)
    async with factory() as session:
        if await conversations.get(session, current.id, cid) is None:
            raise _not_found()
    await runs.cancel(current.id, cid)  # nothing running is fine: cancelling is idempotent
    return Response(status_code=202)


@router.get(
    "/{conversation_id}/messages",
    response_model=MessagePage,
    summary="List messages, newest first",
    tags=["conversations"],
)
async def list_messages(
    conversation_id: str,
    current: CurrentUserDep,
    factory: FactoryDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    cursor: str | None = None,
) -> MessagePage:
    cid = _parse_id(conversation_id)
    try:
        decoded = conversations.decode_cursor(cursor) if cursor else None
    except conversations.InvalidCursorError:
        raise ApiError(400, "BAD_REQUEST", "Invalid cursor.") from None
    async with factory() as session:
        if await conversations.get(session, current.id, cid) is None:
            raise _not_found()
        items, next_cursor = await messages.list_page(
            session, current.id, cid, limit=limit, cursor=decoded
        )
        ratings = await feedback.scores_for(session, current.id, [m.id for m in items])
    return MessagePage(
        items=[MessageOut.from_model(m, ratings.get(m.id)) for m in items],
        next_cursor=next_cursor,
    )
