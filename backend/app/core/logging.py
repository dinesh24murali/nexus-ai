"""Structured JSON logging (structlog) and the request-context middleware.

Privacy rule (TASK-004, AGENTS.md §3.8): logs never carry request bodies, query strings, headers
or message content. The access log records method, path (no query), status, latency and request
id only.
"""

import logging
import re
import secrets
import sys
import time

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import REQUEST_ID_SCOPE_KEY

# Incoming ids are only honoured when they look like ours: never reflect arbitrary header content.
_REQUEST_ID_RE = re.compile(r"^req_[0-9a-f]{16,64}$")

_log = structlog.get_logger("nexus.access")


def configure_logging(level: str = "INFO") -> None:
    shared: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
    ]
    structlog.configure(
        processors=[
            *shared,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelNamesMapping()[level]),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )

    # Third-party loggers (uvicorn, sqlalchemy, ...) go through the same JSON renderer.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=[structlog.stdlib.add_logger_name, *shared],
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(),
            ],
        )
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    # Third-party libraries (httpx, openai, ...) log request URLs, query strings included, at INFO.
    # Default every stdlib logger to WARNING instead of chasing client libraries one by one; only
    # an explicit DEBUG opt-in (local debugging) lowers it. structlog loggers ignore the root level.
    root.setLevel(logging.DEBUG if level == "DEBUG" else logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(level)
    # Our middleware writes the access log; uvicorn's would duplicate it.
    logging.getLogger("uvicorn.access").disabled = True


def new_request_id() -> str:
    return f"req_{secrets.token_hex(8)}"


class RequestContextMiddleware:
    """Pure ASGI (not BaseHTTPMiddleware) so streaming/SSE responses are never buffered."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _REQUEST_ID_RE.match(incoming) else new_request_id()
        scope[REQUEST_ID_SCOPE_KEY] = request_id
        structlog.contextvars.bind_contextvars(request_id=request_id)

        status = 500
        started = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message["headers"] = [
                    *message.get("headers", []),
                    (b"x-request-id", request_id.encode()),
                ]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            _log.info(
                "http_request",
                method=scope["method"],
                path=scope["path"],
                status=status,
                latency_ms=round((time.perf_counter() - started) * 1000, 1),
            )
            structlog.contextvars.clear_contextvars()
