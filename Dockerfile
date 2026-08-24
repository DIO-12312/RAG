# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.12.1 AS uv
FROM docker:29.4.0-cli AS docker-cli

FROM python:3.12.11-slim-bookworm AS build-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=uv /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock LICENSE ./
COPY docs/README.md ./docs/README.md
COPY src ./src

FROM build-base AS runtime-builder

RUN uv sync --frozen --no-dev --no-editable

FROM build-base AS test-builder

RUN uv sync --frozen --all-groups --no-editable

FROM python:3.12.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN addgroup --system rag && \
    adduser --system --ingroup rag rag && \
    mkdir -p /app/data/objects && \
    chown rag:rag /app /app/data /app/data/objects

COPY --chown=rag:rag --from=runtime-builder /app/.venv /app/.venv
COPY --chown=rag:rag scripts/docker_healthcheck.py /app/scripts/docker_healthcheck.py
COPY --chown=rag:rag alembic.ini /app/alembic.ini
COPY --chown=rag:rag migrations /app/migrations

USER rag

CMD ["rag-server"]

FROM runtime AS test

ENV UV_NO_SYNC=1 \
    UV_CACHE_DIR=/tmp/uv-cache

USER root

COPY --from=uv /uv /uvx /bin/
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --chown=rag:rag --from=test-builder /app/.venv /app/.venv
COPY --chown=rag:rag pyproject.toml uv.lock LICENSE Dockerfile docker-compose.yml .dockerignore ./
COPY --chown=rag:rag docs/README.md ./docs/README.md
COPY --chown=rag:rag scripts ./scripts

USER rag

CMD ["python", "-c", "print('rag-test ready')"]
