"""Worker process (`python -m app.worker`).

M1: an idle process that proves the deployment shape (starts, heartbeats, stops cleanly on
SIGTERM). The Postgres job queue and periodic tasks land in TASK-012 (ADR-0007) and plug into `run`.
"""

import asyncio
import contextlib
import signal
from pathlib import Path

import structlog

from app.core.config import get_settings
from app.core.logging import configure_logging

HEARTBEAT_PATH = Path("/tmp/worker-alive")  # noqa: S108 - container-local liveness marker
HEARTBEAT_INTERVAL_S = 10.0

_log = structlog.get_logger("nexus.worker")


async def run(
    stop: asyncio.Event,
    *,
    heartbeat_path: Path = HEARTBEAT_PATH,
    interval_s: float = HEARTBEAT_INTERVAL_S,
) -> None:
    """Run until `stop` is set, touching `heartbeat_path` every `interval_s` (healthcheck)."""
    _log.info("worker_started")
    while not stop.is_set():
        await asyncio.to_thread(heartbeat_path.touch)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
    _log.info("worker_stopped")


async def main() -> None:
    configure_logging(get_settings().log_level)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await run(stop)


if __name__ == "__main__":
    asyncio.run(main())
