"""Readiness checks: Postgres and Qdrant are required; Langfuse is optional (best-effort)."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

import httpx
import psycopg

from app.core.config import Settings

CHECK_TIMEOUT_S = 2.0

Status = Literal["ok", "degraded", "unavailable"]


@dataclass(frozen=True)
class Check:
    name: str
    fn: Callable[[], Awaitable[bool]]
    required: bool = True


async def _run(check: Check) -> bool:
    try:
        return await asyncio.wait_for(check.fn(), timeout=CHECK_TIMEOUT_S)
    except Exception:
        return False


async def run_checks(checks: list[Check]) -> tuple[Status, dict[str, str]]:
    results = await asyncio.gather(*(_run(c) for c in checks))
    report = {
        c.name: ("ok" if ok else "unavailable") for c, ok in zip(checks, results, strict=True)
    }
    if any(not ok and c.required for c, ok in zip(checks, results, strict=True)):
        return "unavailable", report
    if not all(results):
        return "degraded", report
    return "ok", report


def default_checks(settings: Settings) -> list[Check]:
    # psycopg wants a plain libpq URL, not the SQLAlchemy "+psycopg" dialect form.
    pg_url = settings.database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    async def postgres() -> bool:
        async with await psycopg.AsyncConnection.connect(pg_url, connect_timeout=2) as conn:
            await conn.execute("SELECT 1")
        return True

    async def qdrant() -> bool:
        headers = {}
        if key := settings.qdrant_api_key.get_secret_value():
            headers["api-key"] = key
        async with httpx.AsyncClient(timeout=CHECK_TIMEOUT_S) as client:
            return (await client.get(f"{settings.qdrant_url}/readyz", headers=headers)).is_success

    checks = [Check("postgres", postgres), Check("qdrant", qdrant)]

    if settings.langfuse_configured:

        async def langfuse() -> bool:
            async with httpx.AsyncClient(timeout=CHECK_TIMEOUT_S) as client:
                return (await client.get(f"{settings.langfuse_host}/api/public/health")).is_success

        checks.append(Check("langfuse", langfuse, required=False))
    return checks
