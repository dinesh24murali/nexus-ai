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
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

FROM python:3.14-slim-bookworm AS runtime
RUN groupadd --system --gid 10001 nexus \
 && useradd --system --uid 10001 --gid nexus --home-dir /app --shell /usr/sbin/nologin nexus
WORKDIR /app
COPY --from=builder --chown=nexus:nexus /app /app
COPY --chown=nexus:nexus uvicorn_logging.json /app/uvicorn_logging.json
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER nexus
EXPOSE 8000
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--log-config", "/app/uvicorn_logging.json", "--no-server-header"]
