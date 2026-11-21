# syntax=docker/dockerfile:1
# Build context: ./backend. One image serves the API, worker, migrate step and admin CLI.

FROM ghcr.io/astral-sh/uv:0.13.9 AS uv

FROM python:3.14-slim-bookworm AS builder
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first so this layer is cached until the lockfile changes.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
# Prompts are canonical in the repo and loaded from disk at runtime (ADR-0008).
COPY prompts ./prompts
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev
# tiktoken downloads its encoding on first use; bake it in so token counting works offline.
ENV TIKTOKEN_CACHE_DIR=/app/.tiktoken
RUN /app/.venv/bin/python -c "import tiktoken; tiktoken.get_encoding('o200k_base')"

FROM python:3.14-slim-bookworm AS runtime
RUN groupadd --system --gid 10001 nexus \
 && useradd --system --uid 10001 --gid nexus --home-dir /app --shell /usr/sbin/nologin nexus
WORKDIR /app
COPY --from=builder --chown=nexus:nexus /app /app
COPY --chown=nexus:nexus uvicorn_logging.json /app/uvicorn_logging.json
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    TIKTOKEN_CACHE_DIR=/app/.tiktoken
USER nexus
EXPOSE 8000
# --timeout-graceful-shutdown: on SIGTERM uvicorn waits this long for open requests (streaming chat
# runs) before cutting them off. It must exceed the 90 s run drain (ADR-0006) and stay below the
# Compose `stop_grace_period` (120 s).
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--log-config", "/app/uvicorn_logging.json", "--no-server-header", \
     "--timeout-graceful-shutdown", "105"]
