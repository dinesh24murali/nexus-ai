"""What the `generate_document` pipeline reads and writes (TASK-041): the production
`DocumentService`.

Reads come from the message read model (owner-scoped), writes are one transaction that also queues
the render job. The limits live here: documents per user per UTC day (10) and the worst-case cost of
a run against the user's remaining budget and the global cap (doc 08 section 2.5). Every call takes
the run's trusted user id; nothing a user wrote is logged.
"""

import uuid
from datetime import UTC, datetime, time
from decimal import ROUND_UP, Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.repositories import conversations, files, messages
from app.db.repositories import documents as documents_repo
from app.db.repositories import usage as usage_repo
from app.documents.draft import DocumentDraft
from app.graph.intents.generate_document import DocumentRef, LastAnswer
from app.jobs import queue
from app.jobs.handlers import JOBS
from app.llm.pricing import price_for

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


def user_budget_reply(worst: Decimal, remaining: Decimal) -> str:
    return (
        f"I can't start a document right now: it could cost up to ${worst:.2f} and you have "
        f"${max(remaining, Decimal(0)):.2f} left of today's budget. It resets at midnight UTC."
    )


GLOBAL_BUDGET_REPLY = (
    "I can't start a document right now: the service's daily budget is almost used up. "
    "Please try again tomorrow."
)


def _day_start(now: datetime) -> datetime:
    return datetime.combine(now.astimezone(UTC).date(), time.min, tzinfo=UTC)


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
            day = now.astimezone(UTC).date()
            row = await usage_repo.get_day(session, user_id, day)
            mine = row.cost_usd if row is not None else Decimal(0)
            total = await usage_repo.global_cost(session, day)
        worst = worst_case_usd(self._settings)
        remaining = self._settings.user_daily_budget_usd - mine
        if remaining < worst:
            return user_budget_reply(worst, remaining)
        if self._settings.global_daily_budget_usd - total < worst:
            return GLOBAL_BUDGET_REPLY
        return None

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
