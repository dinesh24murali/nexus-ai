from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from app.api import auth, conversations, health, me
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.health import default_checks
from app.core.logging import RequestContextMiddleware, configure_logging
from app.core.runtime_flags import RuntimeFlags
from app.db.session import create_engine, create_session_factory

API_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None) -> FastAPI:
    """App factory. Run with `uvicorn app.main:create_app --factory`."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings.database_url)
        app.state.session_factory = create_session_factory(engine)
        app.state.flags = RuntimeFlags(app.state.session_factory, defaults=settings.flag_defaults())
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(
        title="Nexus AI API",
        lifespan=lifespan,
        # Swagger UI only outside production (the OpenAPI file in docs/02_outputs is the contract).
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )
    app.state.settings = settings
    app.state.readiness_checks = default_checks(settings)

    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)

    api = APIRouter(prefix=API_PREFIX)
    api.include_router(health.router)
    api.include_router(auth.router)
    api.include_router(me.router)
    api.include_router(conversations.router)
    app.include_router(api)
    return app
