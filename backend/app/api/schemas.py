"""Request/response models. The OpenAPI spec in docs/02_outputs is the contract; these mirror it."""

import re
import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    StrictBool,
    StringConstraints,
    model_serializer,
    model_validator,
)


def _no_nul(value: str) -> str:
    """Postgres text cannot hold NUL: it would reach the driver and surface as a 500."""
    if "\x00" in value:
        raise ValueError("must not contain NUL characters")
    return value


DisplayName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=60),
    AfterValidator(_no_nul),
]
Password = Annotated[str, Field(min_length=10, max_length=128)]

# One "@", no whitespace or NUL (Postgres text cannot hold it), something on both sides.
# Deliberately NOT a deliverability check: v1 sends
# no email (no verification or reset), so the address is only a login identifier. Stricter
# validators reject reserved names (.test, .invalid, .local) that the spec's `format: email` allows.
_EMAIL_SHAPE = re.compile(r"^[^@\s\x00]+@[^@\s\x00]+$")


def _normalise_email(value: str) -> str:
    value = value.strip()
    if not _EMAIL_SHAPE.match(value):
        raise ValueError("value is not a valid email address")
    return value.lower()


Email = Annotated[str, Field(max_length=254), AfterValidator(_normalise_email)]


class RegisterRequest(BaseModel):
    invite_code: Annotated[str, Field(min_length=16, max_length=64)] | None = None
    email: Email
    password: Password
    display_name: DisplayName


class LoginRequest(BaseModel):
    email: Email
    # No length cap, per the spec: an over-long password simply fails verification (401).
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str
    memory_enabled: bool
    created_at: datetime


class UsageOut(BaseModel):
    """`Usage` in the OpenAPI spec: today's (UTC) numbers for the current user."""

    day: date
    spend_usd: float
    budget_usd: float
    tokens_used: int
    messages: int
    global_cap_reached: bool
    resets_at: datetime


class AuthResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - OAuth token type, not a secret
    expires_in: int
    user: UserOut


class NoNullFieldsModel(BaseModel):
    """Optional-but-not-nullable fields: omit a field to leave it unset; an explicit null is a
    schema violation (the OpenAPI spec types them as plain string/boolean, not nullable)."""

    @model_validator(mode="after")
    def _no_explicit_null(self) -> Self:
        for name in self.model_fields_set:
            if getattr(self, name) is None:
                raise ValueError(f"{name} must not be null")
        return self


class UpdateMeRequest(NoNullFieldsModel):
    display_name: DisplayName | None = None
    memory_enabled: StrictBool | None = None


class DeleteAccountRequest(BaseModel):
    confirm_email: Email


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: Password


# ───────────────────────────── conversations ─────────────────────────────


Title = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
    AfterValidator(_no_nul),
]


class CreateConversationRequest(NoNullFieldsModel):
    title: Title | None = None


class UpdateConversationRequest(BaseModel):
    title: Title


class ConversationOut(BaseModel):
    id: uuid.UUID
    title: str
    has_active_run: bool
    pending_interrupt: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, c: Any) -> ConversationOut:
        return cls(
            id=c.id,
            title=c.title,
            has_active_run=c.active_run_id is not None,
            pending_interrupt=c.pending_interrupt,
            created_at=c.created_at,
            updated_at=c.updated_at,
        )


class ConversationPage(BaseModel):
    items: list[ConversationOut]
    next_cursor: str | None


# ───────────────────────────────── files ─────────────────────────────────


class FileOut(BaseModel):
    id: uuid.UUID
    filename: str
    mime_type: str
    size_bytes: int
    status: Literal["pending", "processing", "ready", "failed"]
    error: str | None
    page_count: int | None
    chunk_count: int | None
    created_at: datetime

    @classmethod
    def from_model(cls, f: Any) -> FileOut:
        return cls(
            id=f.id,
            filename=f.filename,
            mime_type=f.mime_type,
            size_bytes=f.size_bytes,
            status=f.status,
            error=f.error,
            page_count=f.page_count,
            chunk_count=f.chunk_count,
            created_at=f.created_at,
        )


class FilePage(BaseModel):
    items: list[FileOut]
    next_cursor: str | None


# ──────────────────────────────── memories ────────────────────────────────


class MemoryInput(BaseModel):
    # Mirrors the spec (1..500, no stripping): the memory policy decides what else is refused.
    content: Annotated[str, StringConstraints(min_length=1, max_length=500)]


class MemoryOut(BaseModel):
    id: uuid.UUID
    content: str
    source: Literal["extracted", "chat", "manual"]
    conversation_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_memory(cls, m: Any) -> MemoryOut:
        return cls(
            id=m.id,
            content=m.content,
            source=m.source,
            conversation_id=m.conversation_id,
            created_at=m.created_at,
            updated_at=m.updated_at,
        )


class MemoryPage(BaseModel):
    items: list[MemoryOut]
    next_cursor: str | None


# ───────────────────────────────── chat ─────────────────────────────────


def _not_blank(value: str) -> str:
    """A message of only whitespace is a user error; the spec's `minLength: 1` is looser (same
    documented pattern as conversation titles)."""
    if not value.strip():
        raise ValueError("must not be blank")
    return value


MessageContent = Annotated[
    str, Field(min_length=1, max_length=16_000), AfterValidator(_no_nul), AfterValidator(_not_blank)
]


class SendMessageRequest(BaseModel):
    content: MessageContent


EditedQuery = Annotated[str, Field(min_length=1, max_length=400), AfterValidator(_no_nul)]


class EditedArgs(NoNullFieldsModel):
    """Replacement arguments for an approval decided with `edit` (the spec's `edited_args`)."""

    query: EditedQuery | None = None  # web_search
    queries: Annotated[list[EditedQuery], Field(min_length=1, max_length=6)] | None = None
    memory_id: uuid.UUID | None = None  # forget_memory


class ResumeRunRequest(NoNullFieldsModel):
    """The decision on a pending tool approval (`POST /conversations/{id}/runs/resume`)."""

    interrupt_id: Annotated[str, AfterValidator(_no_nul)]
    decision: Literal["approve", "edit", "reject"]
    edited_args: EditedArgs | None = None


Comment = Annotated[str, Field(max_length=1000), AfterValidator(_no_nul)]


def _not_a_bool(value: Any) -> Any:
    """`true` equals 1 in Python and would pass for a thumbs-up; the spec's score is an integer."""
    if isinstance(value, bool):
        raise ValueError("must be an integer")
    return value


class FeedbackRequest(NoNullFieldsModel):
    # Strict: "1" is not a rating either.
    model_config = ConfigDict(strict=True)

    score: Annotated[Literal[-1, 1], BeforeValidator(_not_a_bool)]
    comment: Comment | None = None

    @model_validator(mode="after")
    def _blank_comment_is_no_comment(self) -> Self:
        if self.comment is not None and not self.comment.strip():
            self.comment = None
        return self


class FeedbackOut(BaseModel):
    message_id: uuid.UUID
    score: Literal[-1, 1]
    comment: str | None
    created_at: datetime


class MessageUsageOut(BaseModel):
    model: str | None
    prompt_tokens: int
    cached_tokens: int
    completion_tokens: int
    cost_usd: float


class MessageOut(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    role: Literal["user", "assistant"]
    content: str
    status: Literal["complete", "cancelled", "failed"]
    finish_reason: str | None
    tool_calls: list[Any]
    citations: list[Any]
    feedback: int | None
    usage: MessageUsageOut | None
    intent: str | None
    documents: list[Any]
    created_at: datetime

    @model_serializer(mode="wrap")
    def _omit_absent_usage(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if data.get("usage") is None:
            data.pop("usage", None)  # the spec's `usage` is an object, never null (user messages)
        return data

    @classmethod
    def from_model(cls, m: Any, feedback: int | None = None) -> MessageOut:
        usage = None
        if m.role == "assistant" and m.model is not None:
            usage = MessageUsageOut(
                model=m.model,
                prompt_tokens=m.prompt_tokens,
                cached_tokens=m.cached_tokens,
                completion_tokens=m.completion_tokens,
                cost_usd=float(m.cost_usd),
            )
        return cls(
            id=m.id,
            conversation_id=m.conversation_id,
            role=m.role,
            content=m.content,
            status=m.status,
            finish_reason=m.finish_reason,
            tool_calls=list(m.tool_calls or []),
            citations=list(m.citations or []),
            feedback=feedback,
            usage=usage,
            intent=None,  # the intent router arrives with M7
            documents=[],
            created_at=m.created_at,
        )


class MessagePage(BaseModel):
    items: list[MessageOut]
    next_cursor: str | None
