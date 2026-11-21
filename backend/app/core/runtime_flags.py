"""Runtime flags: operator kill switches (docs/02_outputs/10_llm_threat_model.md section 3).

Rows in the `runtime_flags` table, set with `python -m app.cli flags set NAME=VALUE`. The API and
worker read them through a 60 s cache, so flipping one needs no restart or drain. Environment
variables only provide boot defaults; a DB row always wins.
"""

import math
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Literal

import structlog
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import RuntimeFlag

Kind = Literal["bool", "text"]

KNOWN_FLAGS: dict[str, Kind] = {
    "TOOLS_DISABLED": "text",  # comma-separated tool names, e.g. web_search,calculator
    "MEMORY_WRITES_DISABLED": "bool",
    "CHAT_MODEL_OVERRIDE": "text",
    "READ_ONLY_MODE": "bool",
    "ROUTER_DISABLED": "bool",  # v1.1
    "INTENTS_DISABLED": "text",  # v1.1, comma-separated intent names
}

CACHE_TTL_S = 60.0

_log = structlog.get_logger("nexus.flags")
Factory = async_sessionmaker[AsyncSession]


def validate(name: str, value: str) -> str:
    """Normalised value for `name`, or ValueError."""
    kind = KNOWN_FLAGS.get(name)
    if kind is None:
        raise ValueError(f"Unknown flag {name!r}. Known: {', '.join(sorted(KNOWN_FLAGS))}")
    value = value.strip()
    if kind == "bool" and value not in {"0", "1"}:
        raise ValueError(f"{name} must be 0 or 1, got {value!r}")
    if kind == "text":
        value = ",".join(part.strip() for part in value.split(",") if part.strip())
    return value


async def set_flag(factory: Factory, name: str, value: str, *, updated_by: str) -> str:
    value = validate(name, value)
    stmt = insert(RuntimeFlag).values(name=name, value=value, updated_by=updated_by)
    stmt = stmt.on_conflict_do_update(
        index_elements=[RuntimeFlag.name],
        set_={"value": value, "updated_by": updated_by, "updated_at": func.now()},
    )
    async with factory() as session, session.begin():
        await session.execute(stmt)
    return value


async def list_flags(factory: Factory) -> dict[str, tuple[str, datetime, str | None]]:
    async with factory() as session:
        rows = (await session.execute(select(RuntimeFlag))).scalars()
        return {r.name: (r.value, r.updated_at, r.updated_by) for r in rows}


class RuntimeFlags:
    def __init__(
        self,
        factory: Factory,
        *,
        defaults: Mapping[str, str],
        ttl_s: float = CACHE_TTL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._factory = factory
        self._defaults = dict(defaults)
        self._ttl_s = ttl_s
        self._clock = clock
        self._snapshot: dict[str, str] | None = None
        self._loaded_at = -math.inf

    async def _refresh(self) -> None:
        try:
            async with self._factory() as session:
                rows = (await session.execute(select(RuntimeFlag))).scalars()
                self._snapshot = {r.name: r.value for r in rows}
        except Exception:
            # A kill switch must not become a new way to take the API down: keep serving the last
            # known values (or the boot defaults) and retry after the next TTL.
            _log.warning("runtime_flags_refresh_failed", exc_info=True)
            if self._snapshot is None:
                self._snapshot = {}
        self._loaded_at = self._clock()

    async def get(self, name: str) -> str:
        if self._snapshot is None or self._clock() - self._loaded_at >= self._ttl_s:
            await self._refresh()
        assert self._snapshot is not None  # noqa: S101 - set by _refresh
        return self._snapshot.get(name, self._defaults.get(name, ""))

    async def enabled(self, name: str) -> bool:
        return await self.get(name) == "1"

    async def csv(self, name: str) -> set[str]:
        return {part.strip() for part in (await self.get(name)).split(",") if part.strip()}

    def _expire_for_tests(self) -> None:
        self._loaded_at = -math.inf
