"""Request/response models. The OpenAPI spec in docs/02_outputs is the contract; these mirror it."""

import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    model_validator,
)

DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
Password = Annotated[str, Field(min_length=10, max_length=128)]

# One "@", no whitespace, something on both sides. Deliberately NOT a deliverability check: v1 sends
# no email (no verification or reset), so the address is only a login identifier. Stricter
# validators reject reserved names (.test, .invalid, .local) that the spec's `format: email` allows.
_EMAIL_SHAPE = re.compile(r"^[^@\s]+@[^@\s]+$")


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


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: Password


# ───────────────────────────── conversations ─────────────────────────────


def _no_nul(value: str) -> str:
    """Postgres text cannot hold NUL: it would reach the driver and surface as a 500."""
    if "\x00" in value:
        raise ValueError("must not contain NUL characters")
    return value


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
