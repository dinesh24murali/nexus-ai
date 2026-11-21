import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from app.api.deps import CurrentUserDep, FactoryDep, RunsDep
from app.api.schemas import (
    ConversationOut,
    ConversationPage,
    CreateConversationRequest,
    UpdateConversationRequest,
)
from app.core.errors import ApiError
from app.db.repositories import conversations as repo

router = APIRouter(prefix="/conversations", tags=["conversations"])


def _not_found() -> ApiError:
    return ApiError(404, "NOT_FOUND", "Conversation not found.")


def _parse_id(raw: str) -> uuid.UUID:
    """A malformed id is just another unknown id (404), as the spec documents; never a 422."""
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise _not_found() from None


async def _create_body(request: Request) -> CreateConversationRequest:
    """The body is optional, but a body that IS sent must be an object: JSON `null` violates the
    spec (the schema is not nullable), which a `Model | None` parameter would silently accept."""
    raw = await request.body()
    if not raw.strip():
        return CreateConversationRequest()
    try:
        return CreateConversationRequest.model_validate_json(raw)
    except ValidationError as exc:
        # Same 422 envelope as every other body; the handler drops pydantic's `input`/`ctx`.
        raise RequestValidationError(
            [{**e, "loc": ("body", *e["loc"])} for e in exc.errors()]
        ) from None


@router.get(
    "", response_model=ConversationPage, summary="List conversations, most recently updated first"
)
async def list_conversations(
    current: CurrentUserDep,
    factory: FactoryDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    cursor: str | None = None,
) -> ConversationPage:
    try:
        # `?cursor=` (empty) is schema-valid and means "no cursor": the first page.
        decoded = repo.decode_cursor(cursor) if cursor else None
    except repo.InvalidCursorError:
        raise ApiError(400, "BAD_REQUEST", "Invalid cursor.") from None
    async with factory() as session:
        items, next_cursor = await repo.list_page(session, current.id, limit=limit, cursor=decoded)
    return ConversationPage(
        items=[ConversationOut.from_model(c) for c in items], next_cursor=next_cursor
    )


@router.post(
    "",
    status_code=201,
    response_model=ConversationOut,
    summary="Create an empty conversation",
    openapi_extra={
        "requestBody": {
            "required": False,
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/CreateConversationRequest"}
                }
            },
        }
    },
)
async def create_conversation(
    current: CurrentUserDep,
    factory: FactoryDep,
    body: Annotated[CreateConversationRequest, Depends(_create_body)],
) -> ConversationOut:
    async with factory() as session, session.begin():
        conversation = await repo.create(session, current.id, body.title)
    return ConversationOut.from_model(conversation)


@router.get("/{conversation_id}", response_model=ConversationOut, summary="Get a conversation")
async def get_conversation(
    conversation_id: str, current: CurrentUserDep, factory: FactoryDep
) -> ConversationOut:
    cid = _parse_id(conversation_id)
    async with factory() as session:
        conversation = await repo.get(session, current.id, cid)
    if conversation is None:
        raise _not_found()
    return ConversationOut.from_model(conversation)


@router.patch("/{conversation_id}", response_model=ConversationOut, summary="Rename a conversation")
async def rename_conversation(
    conversation_id: str,
    body: UpdateConversationRequest,
    current: CurrentUserDep,
    factory: FactoryDep,
) -> ConversationOut:
    cid = _parse_id(conversation_id)
    async with factory() as session, session.begin():
        conversation = await repo.rename(session, current.id, cid, body.title)
    if conversation is None:
        raise _not_found()
    return ConversationOut.from_model(conversation)


@router.delete("/{conversation_id}", status_code=204, summary="Delete a conversation")
async def delete_conversation(
    conversation_id: str, current: CurrentUserDep, factory: FactoryDep, runs: RunsDep
) -> Response:
    cid = _parse_id(conversation_id)
    # A run still writing into the conversation would hit a foreign-key error (or resurrect its
    # checkpoints), so stop it and wait until it has been saved. A no-op for other users' ids.
    await runs.cancel_and_wait(current.id, cid)
    async with factory() as session, session.begin():
        deleted = await repo.delete_with_checkpoints(session, current.id, cid)
    if not deleted:
        raise _not_found()
    return Response(status_code=204)
