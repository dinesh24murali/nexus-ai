"""Runtime flags (kill switches): DB rows read through a 60 s cache; env gives boot defaults."""

from collections.abc import AsyncIterator
from typing import Any

import psycopg
import pytest
from app.core.runtime_flags import KNOWN_FLAGS, RuntimeFlags, set_flag
from app.db.session import create_engine, create_session_factory
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.integration


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
async def factory(db_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_engine(db_url)
    yield create_session_factory(engine)
    await engine.dispose()


async def test_returns_boot_default_when_no_row(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    flags = RuntimeFlags(factory, defaults={"READ_ONLY_MODE": "0", "TOOLS_DISABLED": "web_search"})
    assert await flags.get("READ_ONLY_MODE") == "0"
    assert await flags.get("TOOLS_DISABLED") == "web_search"
    assert await flags.get("CHAT_MODEL_OVERRIDE") == ""


async def test_db_value_overrides_default_after_the_cache_expires(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = FakeClock()
    flags = RuntimeFlags(factory, defaults={"READ_ONLY_MODE": "0"}, ttl_s=60, clock=clock)
    assert await flags.get("READ_ONLY_MODE") == "0"  # cache populated

    await set_flag(factory, "READ_ONLY_MODE", "1", updated_by="tester")

    clock.now += 59
    assert await flags.get("READ_ONLY_MODE") == "0", "still inside the 60 s cache window"
    clock.now += 2
    assert await flags.get("READ_ONLY_MODE") == "1", "visible after the cache expires"


async def test_set_flag_rejects_unknown_names(factory: async_sessionmaker[AsyncSession]) -> None:
    with pytest.raises(ValueError, match="Unknown flag"):
        await set_flag(factory, "NOT_A_FLAG", "1", updated_by="tester")


@pytest.mark.parametrize("flag", ["READ_ONLY_MODE", "MEMORY_WRITES_DISABLED", "ROUTER_DISABLED"])
async def test_boolean_flags_only_accept_0_or_1(
    factory: async_sessionmaker[AsyncSession], flag: str
) -> None:
    with pytest.raises(ValueError, match="0 or 1"):
        await set_flag(factory, flag, "yes", updated_by="tester")


async def test_csv_helper_parses_lists(factory: async_sessionmaker[AsyncSession]) -> None:
    await set_flag(factory, "TOOLS_DISABLED", " web_search , calculator,", updated_by="tester")
    flags = RuntimeFlags(factory, defaults={}, ttl_s=0)
    assert await flags.csv("TOOLS_DISABLED") == {"web_search", "calculator"}


async def test_serves_stale_values_if_the_database_is_unreachable(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """A kill switch must not become a new way to take the API down."""
    clock = FakeClock()
    flags = RuntimeFlags(factory, defaults={"READ_ONLY_MODE": "0"}, ttl_s=60, clock=clock)
    await set_flag(factory, "READ_ONLY_MODE", "1", updated_by="tester")
    assert await flags.get("READ_ONLY_MODE") == "1"

    class Broken:
        def __call__(self) -> Any:
            raise ConnectionError("db down")

    flags._factory = Broken()  # type: ignore[assignment]
    clock.now += 120
    assert await flags.get("READ_ONLY_MODE") == "1"


def test_known_flags_match_the_threat_model() -> None:
    assert {
        "TOOLS_DISABLED",
        "MEMORY_WRITES_DISABLED",
        "CHAT_MODEL_OVERRIDE",
        "READ_ONLY_MODE",
        "ROUTER_DISABLED",
        "INTENTS_DISABLED",
    } == set(KNOWN_FLAGS)


def test_running_api_picks_up_a_flag_set_from_the_cli_without_restart(
    client: TestClient, db: psycopg.Connection[Any]
) -> None:
    """The acceptance criterion: set with the CLI (a separate process), seen by the live API."""
    from app.cli import main

    flags: RuntimeFlags = client.app.state.flags  # type: ignore[attr-defined]
    assert client.portal.call(flags.get, "READ_ONLY_MODE") == "0"  # type: ignore[union-attr]

    assert main(["flags", "set", "READ_ONLY_MODE=1"], settings=client.app.state.settings) == 0  # type: ignore[attr-defined]

    # Same running app instance, no restart: expire its cache (60 s in production).
    flags._expire_for_tests()
    assert client.portal.call(flags.get, "READ_ONLY_MODE") == "1"  # type: ignore[union-attr]
