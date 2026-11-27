"""files: status lifecycle and unknown counts

`files.status` becomes `pending -> processing -> ready | failed` with a CHECK (it defaulted to the
unused value `uploaded`), and `page_count` / `chunk_count` become nullable: the API reports null
until ingestion knows them (a 0 would read as "empty file").

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-21

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE files SET status = 'pending' WHERE status = 'uploaded'")
    op.alter_column("files", "status", server_default="pending")
    op.create_check_constraint(
        "status", "files", "status IN ('pending', 'processing', 'ready', 'failed')"
    )
    for column in ("page_count", "chunk_count"):
        op.alter_column("files", column, nullable=True, server_default=None)
    op.execute("UPDATE files SET page_count = NULL, chunk_count = NULL WHERE status <> 'ready'")


def downgrade() -> None:
    raise NotImplementedError("migrations are forward-only (backend/AGENTS.md)")
