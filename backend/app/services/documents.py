"""What the `generate_document` pipeline reads and writes (TASK-041): the production
`DocumentService`.

Reads come from the message read model (owner-scoped), writes are one transaction that also queues
the render job. The limits live here: documents per user per UTC day (10) and the worst-case cost of
a run against the user's remaining budget and the global cap (doc 08 section 2.5). Every call takes
the run's trusted user id; nothing a user wrote is logged.
"""

import asyncio
import uuid
from datetime import UTC, datetime, time
from decimal import ROUND_UP, Decimal
from pathlib import Path

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import ApiError
from app.db.models import Document
from app.db.repositories import conversations, files, messages
from app.db.repositories import documents as documents_repo
from app.db.repositories import usage as usage_repo
from app.documents import storage
from app.documents.draft import DocumentDraft
from app.graph.intents.generate_document import DocumentRef, LastAnswer
from app.jobs import queue
from app.jobs.handlers import JOBS
from app.llm.pricing import price_for

_log = structlog.get_logger("nexus.documents")

Factory = async_sessionmaker[AsyncSession]

# Doc 08 section 2.5: the material sent to the model is capped at 16k tokens plus the prompt, and
# the draft at 4k output tokens (reasoning included). The worst case follows from those two numbers.
WORST_CASE_INPUT_TOKENS = 17_000
WORST_CASE_OUTPUT_TOKENS = 4_000
DEFAULT_CHAT_TITLE = "New chat"


def worst_case_usd(settings: Settings) -> Decimal:
    """The most one thread or brief document can cost, by construction (no cache discount)."""
    price = price_for(settings.llm_long_form_model)
    cost = (
        Decimal(WORST_CASE_INPUT_TOKENS) * price.input
        + Decimal(WORST_CASE_OUTPUT_TOKENS) * price.output
    ) / Decimal(1_000_000)
    return cost.quantize(Decimal("0.001"), rounding=ROUND_UP)


def day_limit_reply(limit: int) -> str:
    return (
        f"You've reached today's limit of {limit} documents. "
        "It resets at midnight UTC; the documents you made are still yours."
    )


def user_budget_reply(worst: Decimal, remaining: Decimal, what: str = "a document") -> str:
    return (
        f"I can't start {what} right now: it could cost up to ${worst:.2f} and you have "
        f"${max(remaining, Decimal(0)):.2f} left of today's budget. It resets at midnight UTC."
    )


def global_budget_reply(what: str = "a document") -> str:
    return (
        f"I can't start {what} right now: the service's daily budget is almost used up. "
        "Please try again tomorrow."
    )


GLOBAL_BUDGET_REPLY = global_budget_reply()


async def budget_refusal(
    session: AsyncSession,
    settings: Settings,
    user_id: uuid.UUID,
    worst: Decimal,
    *,
    what: str = "a document",
) -> str | None:
    """None when a run that could cost up to `worst` fits both the user's remaining budget and the
    global cap, else the reply that refuses it (doc 08 section 2.5). Shared by documents and
    research: a run that has started is never stopped for budget reasons, so this is the check."""
    day = datetime.now(UTC).date()
    row = await usage_repo.get_day(session, user_id, day)
    mine = row.cost_usd if row is not None else Decimal(0)
    total = await usage_repo.global_cost(session, day)
    remaining = settings.user_daily_budget_usd - mine
    if remaining < worst:
        return user_budget_reply(worst, remaining, what)
    if settings.global_daily_budget_usd - total < worst:
        return global_budget_reply(what)
    return None


def _day_start(now: datetime) -> datetime:
    return datetime.combine(now.astimezone(UTC).date(), time.min, tzinfo=UTC)


def _not_found() -> ApiError:
    # One answer for an unknown id, a malformed one and another user's document (never a 403).
    return ApiError(404, "NOT_FOUND", "Document not found.")


class DocumentsService:
    def __init__(self, factory: Factory, settings: Settings) -> None:
        self._factory = factory
        self._settings = settings

    async def check(self, user_id: uuid.UUID, kind: str) -> str | None:
        """None when a document of this kind may start; otherwise the reply that refuses it."""
        now = datetime.now(UTC)
        async with self._factory() as session:
            made = await documents_repo.count_since(session, user_id, _day_start(now))
            if made >= self._settings.documents_per_day:
                return day_limit_reply(self._settings.documents_per_day)
            if kind == "answer":
                return None  # no model call: about $0, nothing to reserve
            return await budget_refusal(
                session, self._settings, user_id, worst_case_usd(self._settings)
            )

    async def last_answer(
        self, user_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> LastAnswer | None:
        async with self._factory() as session:
            answer = await messages.last_chat_answer(session, user_id, conversation_id)
            if answer is None:
                return None
            conversation = await conversations.get(session, user_id, conversation_id)
        title = conversation.title if conversation else None
        return LastAnswer(
            text=answer.content,
            citations=[dict(c) for c in answer.citations or []],
            title_hint=None if title in (None, DEFAULT_CHAT_TITLE) else title,
        )

    async def recent_messages(
        self, user_id: uuid.UUID, conversation_id: uuid.UUID, limit: int
    ) -> list[tuple[str, str]]:
        async with self._factory() as session:
            return await messages.recent_texts(session, user_id, conversation_id, limit)

    async def create(
        self,
        user_id: uuid.UUID,
        *,
        conversation_id: uuid.UUID | None,
        message_id: uuid.UUID | None,
        kind: str,
        draft: DocumentDraft,
    ) -> DocumentRef | None:
        """The row and its render job in ONE transaction, under the user's row lock so two
        conversations finishing together cannot both take the last slot of the day."""
        async with self._factory() as session, session.begin():
            await files.lock_user(session, user_id)
            made = await documents_repo.count_since(session, user_id, _day_start(datetime.now(UTC)))
            if made >= self._settings.documents_per_day:
                return None
            row = await documents_repo.create(
                session,
                user_id,
                document_id=uuid.uuid4(),
                conversation_id=conversation_id,
                message_id=message_id,
                title=draft.title,
                kind=kind,
                draft=draft.model_dump(),
            )
            await queue.enqueue(
                session,
                "render_document",
                {"document_id": str(row.id)},
                f"render:{row.id}",
                user_id=user_id,
                max_attempts=JOBS["render_document"].max_attempts,
            )
        return DocumentRef(
            id=row.id,
            title=row.title,
            kind=row.kind,
            conversation_id=row.conversation_id,
            message_id=row.message_id,
            created_at=row.created_at,
        )

    # ----- the documents API (TASK-042): every call is scoped to the caller's own rows -----

    async def delete(self, user_id: uuid.UUID, document_id: uuid.UUID) -> None:
        """Remove a document and its PDF. The row goes first: a render that is still running then
        finds no row to mark ready and removes the file it wrote (`render_document`). The file goes
        after the commit; if that fails the PDF is an orphan no query can reach, so it is logged,
        not raised (the same order as `FileService.delete`)."""
        async with self._factory() as session, session.begin():
            found, relative = await documents_repo.delete(session, user_id, document_id)
        if not found:
            raise _not_found()
        try:
            await asyncio.to_thread(storage.remove, self._settings.uploads_dir, relative)
        except OSError:
            _log.warning("document_delete_disk_failed", document_id=str(document_id), exc_info=True)
        _log.info("document_deleted", document_id=str(document_id))

    async def pdf_for_download(
        self, user_id: uuid.UUID, document_id: uuid.UUID
    ) -> tuple[Document, Path]:
        """The caller's rendered document and where its PDF is. 404 for an unknown or foreign id
        and for a `ready` row whose file is gone (waiting would not help); 409 while it is still
        being made or when the render failed."""
        async with self._factory() as session:
            row = await documents_repo.get(session, user_id, document_id)
        if row is None:
            raise _not_found()
        if row.status != "ready":
            message = (
                "This document could not be made, so there is no PDF."
                if row.status == "failed"
                else "The PDF is still being prepared."
            )
            raise ApiError(409, "DOCUMENT_NOT_READY", message)
        path = await asyncio.to_thread(self._existing_pdf, row.storage_path)
        if path is None:
            _log.warning("document_file_missing", document_id=str(document_id))
            raise _not_found()
        return row, path

    def _existing_pdf(self, relative: str | None) -> Path | None:
        path = storage.resolve(self._settings.uploads_dir, relative)
        return path if path is not None and path.is_file() else None
