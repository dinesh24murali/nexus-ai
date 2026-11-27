"""messages: which pipeline answered, and how it was chosen

`messages.intent` (general_chat | manage_memory | generate_document | deep_research) and
`messages.route_source` (router | command | fallback), both nullable: messages from before the
intent router (ADR-0010) have neither, and neither has an answer that needed no pipeline except for
`route_source = 'command'` (`/help`, a usage reply).

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-22

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("intent", sa.Text(), nullable=True))
    op.add_column("messages", sa.Column("route_source", sa.Text(), nullable=True))
    op.create_check_constraint(
        "intent",
        "messages",
        "intent IS NULL OR intent IN "
        "('general_chat', 'manage_memory', 'generate_document', 'deep_research')",
    )
    op.create_check_constraint(
        "route_source",
        "messages",
        "route_source IS NULL OR route_source IN ('router', 'command', 'fallback')",
    )


def downgrade() -> None:
    raise NotImplementedError("migrations are forward-only (backend/AGENTS.md)")
