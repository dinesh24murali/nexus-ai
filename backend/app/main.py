import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from functools import cache

import structlog
from fastapi import APIRouter, FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.api import auth, chat, conversations, feedback, files, health, me, memories
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.health import default_checks
from app.core.logging import RequestContextMiddleware, configure_logging
from app.core.runtime_flags import RuntimeFlags
from app.core.shutdown import chain_sigterm
from app.db.session import create_engine, create_session_factory
from app.llm.factory import build_embeddings_client
from app.llm.tracing import Tracer
from app.memory.manage import memory_manager_for
from app.memory.read import memory_lookup_for
from app.rag.embedder import Embedder
from app.rag.retriever import lazy_retriever
from app.services.documents import DocumentsService
from app.services.feedback import FeedbackService
from app.services.files import FileService, recent_files_for
from app.services.memories import MemoryService
from app.services.runs import DRAIN_TIMEOUT_S, RunService, graph_factory_for

_log = structlog.get_logger("nexus.app")

API_PREFIX = "/api/v1"


def lazy_embedder(settings: Settings) -> Callable[[], Embedder]:
    """The embedder, built on first use and then reused: the API starts without an embeddings key
    and a run that needs one fails like a chat run would."""

    @cache
    def get() -> Embedder:
        return Embedder(build_embeddings_client(settings), settings.llm_embedding_model)

    return get


def create_app(settings: Settings | None = None) -> FastAPI:
    """App factory. Run with `uvicorn app.main:create_app --factory`."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings.database_url)
        app.state.session_factory = create_session_factory(engine)
        app.state.flags = RuntimeFlags(app.state.session_factory, defaults=settings.flag_defaults())

        # LangGraph's own connection pool (the checkpointer needs autocommit and no prepared
        # statements). Opening does not wait for a connection, so a database that is down at boot
        # does not stop the API from starting; /readyz reports it.
        pool = AsyncConnectionPool(
            conninfo=settings.database_url.replace("postgresql+psycopg://", "postgresql://", 1),
            max_size=10,
            open=False,
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        )
        await pool.open()
        tracer = Tracer(settings)
        embedder = lazy_embedder(settings)  # shared by the memory lookup and the memories API
        runs = RunService(
            factory=app.state.session_factory,
            settings=settings,
            graph_factory=graph_factory_for(
                settings,
                AsyncPostgresSaver(pool),  # type: ignore[arg-type]
                retriever=lazy_retriever(settings),
                recent_files=recent_files_for(app.state.session_factory),
                memories=memory_lookup_for(app.state.session_factory, settings, embedder),
                memory_manager=memory_manager_for(app.state.session_factory, settings, embedder),
                document_service=DocumentsService(app.state.session_factory, settings),
            ),
            flags=app.state.flags,
            tracer=tracer,
        )
        app.state.runs = runs
        app.state.feedback = FeedbackService(app.state.session_factory, tracer)
        app.state.files = FileService(app.state.session_factory, settings)
        app.state.memories = MemoryService(app.state.session_factory, settings, embedder)
        restore_sigterm = chain_sigterm(runs.begin_drain)  # refuse new runs from the first moment
        try:
            yield
        finally:
            restore_sigterm()
            # Runs whose client already left are not "open requests", so uvicorn does not wait
            # for them: wait here, then save whatever is left as failed (ADR-0006).
            await runs.drain(timeout_s=DRAIN_TIMEOUT_S)
            await pool.close()
            await asyncio.to_thread(tracer.shutdown)
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
    api.include_router(chat.router)
    api.include_router(feedback.router)
    api.include_router(files.router)
    api.include_router(memories.router)
    app.include_router(api)
    return app
