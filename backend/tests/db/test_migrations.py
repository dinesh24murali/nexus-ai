import psycopg
import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from app.db.base import Base
from app.migrate import migrate
from sqlalchemy import create_engine, inspect

pytestmark = pytest.mark.integration

APP_TABLES = {
    "users",
    "invites",
    "refresh_tokens",
    "conversations",
    "messages",
    "files",
    "feedback",
    "usage_daily",
    "jobs",
    "runtime_flags",
    "rate_limits",
}
LANGGRAPH_TABLES = {"checkpoints", "checkpoint_blobs", "checkpoint_writes", "store"}
# `store_vectors` is the memory store's embedding index (TASK-025).
LANGGRAPH_BOOKKEEPING = {
    "checkpoint_migrations",
    "store_migrations",
    "store_vectors",
    "vector_migrations",
}


def libpq(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def table_names(url: str) -> set[str]:
    with psycopg.connect(libpq(url)) as conn:
        rows = conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        ).fetchall()
    return {r[0] for r in rows}


def index_defs(url: str) -> dict[str, str]:
    with psycopg.connect(libpq(url)) as conn:
        rows = conn.execute(
            "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'"
        ).fetchall()
    return {r[0]: r[1] for r in rows}


def test_upgrade_head_creates_app_and_langgraph_tables(empty_db_url: str) -> None:
    assert table_names(empty_db_url) == set()
    migrate(empty_db_url)
    names = table_names(empty_db_url)
    assert names >= APP_TABLES
    assert names >= LANGGRAPH_TABLES
    assert {"store_vectors"} <= names  # the memory index, created with the pgvector extension


def test_migrate_is_idempotent(empty_db_url: str) -> None:
    migrate(empty_db_url)
    before = table_names(empty_db_url)
    migrate(empty_db_url)  # second run: no error, no change
    assert table_names(empty_db_url) == before


def test_required_indexes_exist(empty_db_url: str) -> None:
    migrate(empty_db_url)
    defs = " || ".join(index_defs(empty_db_url).values()).lower()
    # Architecture §3.3
    for fragment in (
        "on public.conversations using btree (user_id, updated_at desc, id)",
        "on public.conversations using btree (run_heartbeat_at) where (active_run_id is not null)",
        "on public.invites using btree (code_hash)",
        "on public.messages using btree (conversation_id, created_at, id)",
        "on public.files using btree (user_id, created_at desc)",
        "on public.files using btree (user_id, sha256)",
        "on public.jobs using btree (status, run_after) where (status = 'queued'::text)",
        "on public.jobs using btree (locked_at) where (status = 'running'::text)",
        "on public.refresh_tokens using btree (user_id)",
        "on public.refresh_tokens using btree (family_id)",
    ):
        assert fragment in defs, f"missing index: {fragment}"


def test_unique_constraints(empty_db_url: str) -> None:
    migrate(empty_db_url)
    engine = create_engine(empty_db_url)
    insp = inspect(engine)
    uniques = {
        t: {tuple(u["column_names"]) for u in insp.get_unique_constraints(t)}
        | {tuple(i["column_names"]) for i in insp.get_indexes(t) if i["unique"]}
        for t in ("users", "invites", "refresh_tokens", "jobs", "files", "feedback")
    }
    assert ("email",) in uniques["users"]
    assert ("code_hash",) in uniques["invites"]
    assert ("token_hash",) in uniques["refresh_tokens"]
    assert ("idempotency_key",) in uniques["jobs"]
    assert ("user_id", "sha256") in uniques["files"]
    assert ("message_id", "user_id") in uniques["feedback"]
    engine.dispose()


def _owned_by_langgraph(diff_entry: tuple[object, ...]) -> bool:
    """True for diffs about LangGraph's own tables or their indexes (not in our metadata)."""
    obj = diff_entry[1]
    table = getattr(obj, "table", obj)
    return getattr(table, "name", None) in LANGGRAPH_TABLES | LANGGRAPH_BOOKKEEPING


def test_models_and_migrations_do_not_drift(empty_db_url: str) -> None:
    migrate(empty_db_url)
    engine = create_engine(empty_db_url)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True})
        diff = [d for d in compare_metadata(ctx, Base.metadata) if not _owned_by_langgraph(d)]
    engine.dispose()
    assert diff == [], f"models and migrations disagree: {diff}"


def test_email_is_case_insensitive(empty_db_url: str) -> None:
    migrate(empty_db_url)
    with psycopg.connect(libpq(empty_db_url), autocommit=True) as conn:
        conn.execute(
            "INSERT INTO users (email, password_hash, display_name) VALUES ('A@B.co', 'h', 'A')"
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO users (email, password_hash, display_name) VALUES ('a@b.CO', 'h', 'A')"
            )
