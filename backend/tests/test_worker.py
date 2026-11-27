import asyncio
from pathlib import Path

from app.worker import run


async def test_worker_heartbeats_and_stops_cleanly(tmp_path: Path) -> None:
    heartbeat = tmp_path / "alive"
    stop = asyncio.Event()
    task = asyncio.create_task(run(stop, heartbeat_path=heartbeat, interval_s=0.01))

    for _ in range(100):
        if heartbeat.exists():
            break
        await asyncio.sleep(0.01)
    assert heartbeat.exists()

    first = heartbeat.stat().st_mtime_ns
    await asyncio.sleep(0.05)
    assert heartbeat.stat().st_mtime_ns > first, "heartbeat must be refreshed"

    stop.set()
    await asyncio.wait_for(task, timeout=1)
