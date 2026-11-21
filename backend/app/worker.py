"""Worker process (`python -m app.worker`): the job queue consumer and the periodic tasks.

* `concurrency` claim loops pull jobs from the Postgres queue (ADR-0007). While a job runs, a
  heartbeat task refreshes its `locked_at` (so a job longer than the 2 min stale window is not
  requeued while its worker is alive), and the handler runs under the kind's hard timeout. A worker
  that finds it no longer holds its job (it was requeued and claimed elsewhere) stops working on it
  and writes nothing.
* Periodic tasks (`jobs/periodic.py`) run on fixed intervals.
* On SIGTERM it stops claiming, waits up to `shutdown_grace_s` for running jobs, and hands the rest
  back to the queue with their attempt refunded.
* A liveness file is touched every few seconds (Compose healthcheck).
"""

import asyncio
import contextlib
import os
import signal
import socket
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import structlog

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import create_engine, create_session_factory
from app.jobs import queue
from app.jobs.handlers import JOBS, JobContext, JobSpec
from app.jobs.periodic import Task, run_periodic, standard_tasks
from app.llm.tracing import Tracer

HEARTBEAT_PATH = Path("/tmp/worker-alive")  # noqa: S108 - container-local liveness marker
HEARTBEAT_INTERVAL_S = 10.0

_log = structlog.get_logger("nexus.worker")


@dataclass(frozen=True)
class WorkerConfig:
    concurrency: int = 4  # doc 08 section 9
    poll_interval_s: float = 1.0
    job_heartbeat_s: float = 30.0  # ADR-0007
    shutdown_grace_s: float = 100.0  # Compose stop_grace_period is 120 s


def default_name() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


async def _keep_alive(
    ctx: JobContext,
    job_id: int,
    worker_id: str,
    handler: asyncio.Future[None],
    lost: asyncio.Event,
    interval_s: float,
) -> None:
    """Refresh the job's lease until cancelled; cancel the handler if the job was taken from us."""
    while True:
        await asyncio.sleep(interval_s)
        try:
            holding = await queue.heartbeat(ctx.factory, job_id, worker_id)
        except Exception:
            _log.warning("job_heartbeat_failed", job_id=job_id, exc_info=True)
            continue  # a database blip: the next beat retries, the stale window is 4 beats wide
        if not holding:
            _log.warning("job_lost", job_id=job_id)
            lost.set()
            handler.cancel()
            return


async def _execute(
    ctx: JobContext, job: queue.ClaimedJob, worker_id: str, spec: JobSpec, config: WorkerConfig
) -> None:
    handler = asyncio.ensure_future(spec.handler(ctx, job))
    lost = asyncio.Event()
    beat = asyncio.create_task(
        _keep_alive(ctx, job.id, worker_id, handler, lost, config.job_heartbeat_s)
    )
    deadline = asyncio.timeout(spec.timeout_s)
    try:
        async with deadline:
            await handler
    except asyncio.CancelledError:
        if lost.is_set():
            return  # someone else owns the job now; completing or failing it would be wrong
        handler.cancel()
        # Shutting down: hand the job back so another worker runs it, without using an attempt.
        await asyncio.shield(queue.release(ctx.factory, job.id, worker_id))
        raise
    except Exception as exc:
        reason = (
            f"timed out after {spec.timeout_s:g}s"
            if deadline.expired()
            else f"{type(exc).__name__}: {exc}"
        )
        _log.exception("job_failed", job_id=job.id, kind=job.kind, attempt=job.attempts)
        await queue.fail(ctx.factory, job, worker_id, reason)
    else:
        await queue.complete(ctx.factory, job.id, worker_id)
        _log.info("job_done", job_id=job.id, kind=job.kind, attempt=job.attempts)
    finally:
        beat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await beat


async def _job_loop(
    ctx: JobContext,
    jobs: Mapping[str, JobSpec],
    worker_id: str,
    stop: asyncio.Event,
    config: WorkerConfig,
) -> None:
    kinds = list(jobs)
    while not stop.is_set():
        try:
            job = await queue.claim(ctx.factory, worker_id, kinds=kinds)
        except Exception:
            _log.exception("job_claim_failed")
            job = None
        if job is None:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=config.poll_interval_s)
            continue
        await _execute(ctx, job, worker_id, jobs[job.kind], config)


async def run(
    stop: asyncio.Event,
    *,
    heartbeat_path: Path = HEARTBEAT_PATH,
    interval_s: float = HEARTBEAT_INTERVAL_S,
    ctx: JobContext | None = None,
    jobs: Mapping[str, JobSpec] | None = None,
    config: WorkerConfig | None = None,
    tasks: Sequence[Task] | None = None,
    name: str | None = None,
) -> None:
    """Run until `stop` is set. Without `ctx` it only touches `heartbeat_path` (deployment shape);
    with one it also consumes jobs and runs the periodic tasks."""
    _log.info("worker_started")
    config = config or WorkerConfig()
    loops: list[asyncio.Task[None]] = []
    background: list[asyncio.Task[None]] = []
    if ctx is not None:
        registry = JOBS if jobs is None else jobs
        worker_name = name or default_name()
        loops = [
            asyncio.create_task(_job_loop(ctx, registry, f"{worker_name}:{slot}", stop, config))
            for slot in range(config.concurrency)
        ]
        periodic = standard_tasks(ctx.settings, ctx.factory) if tasks is None else tasks
        background = [asyncio.create_task(run_periodic(t, stop)) for t in periodic]

    while not stop.is_set():
        await asyncio.to_thread(heartbeat_path.touch)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval_s)

    if loops:
        _, pending = await asyncio.wait(loops, timeout=config.shutdown_grace_s)
        for task in pending:
            task.cancel()  # each one releases its job before it exits
        await asyncio.gather(*pending, return_exceptions=True)
    await asyncio.gather(*background, return_exceptions=True)
    _log.info("worker_stopped")


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_engine(settings.database_url)
    tracer = Tracer(settings)
    ctx = JobContext(factory=create_session_factory(engine), settings=settings, tracer=tracer)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        await run(stop, ctx=ctx)
    finally:
        tracer.shutdown()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
