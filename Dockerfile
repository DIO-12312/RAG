FROM ghcr.io/astral-sh/uv:0.12.1 AS uv

FROM python:3.12.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

COPY --from=uv /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock LICENSE ./
COPY docs/README.md ./docs/README.md
COPY src ./src

RUN uv sync --frozen --no-dev && \
    addgroup --system rag && \
    adduser --system --ingroup rag rag && \
    mkdir -p /app/data/objects && \
    chown -R rag:rag /app

USER rag

CMD ["rag-server"]
