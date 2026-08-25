VERSION --no-implicit-ignore --use-function-keyword 0.8

FROM alpine:3.20

# Export the pinned uv binary used by every Python build target; no local files are modified.
uv-bin:
    FROM ghcr.io/astral-sh/uv:0.12.1
    SAVE ARTIFACT /uv

# Install locked dependencies on Python 3.12.11 and cache them independently from source changes.
python-deps:
    FROM python:3.12.11-slim-bookworm
    COPY +uv-bin/uv /usr/local/bin/uv
    WORKDIR /workspace
    ENV UV_LINK_MODE=copy
    COPY pyproject.toml uv.lock LICENSE ./
    COPY docs/README.md ./docs/README.md
    RUN uv sync --frozen --group dev --no-install-project

# Assemble the complete offline workspace without reading .env or contacting production services.
python-workspace:
    FROM +python-deps
    COPY src ./src
    COPY proto ./proto
    COPY scripts ./scripts
    COPY migrations ./migrations
    COPY tests ./tests
    COPY docs ./docs
    COPY .github ./.github
    COPY .githooks ./.githooks
    COPY Earthfile Makefile Dockerfile docker-compose.yml alembic.ini ./
    COPY .dockerignore .gitattributes ./
    RUN uv sync --frozen --group dev

# Regenerate protobuf code, verify it, and export only successful generated files to the host.
proto:
    FROM +python-workspace
    RUN uv run python scripts/generate_proto.py
    RUN uv run python scripts/check_generated.py
    SAVE ARTIFACT src/rag_mvp/rpc/generated/* AS LOCAL src/rag_mvp/rpc/generated/

# Verify protobuf generated files without modifying the host workspace.
proto-check:
    FROM +python-workspace
    RUN uv run python scripts/check_generated.py

# Check Python imports, style rules, and common correctness errors without rewriting files.
ruff-check:
    FROM +python-workspace
    RUN uv run ruff check src tests scripts migrations

# Verify Ruff formatting without rewriting files.
format-check:
    FROM +python-workspace
    RUN uv run ruff format --check src tests scripts migrations

# Run strict static type checking for production code and executable scripts.
type-check:
    FROM +python-workspace
    RUN uv run mypy src scripts migrations

# Aggregate all non-mutating source-quality checks for the public make lint command.
lint:
    BUILD +ruff-check
    BUILD +format-check
    BUILD +type-check
    BUILD +proto-check

# Run unit, contract, and Fake-backed functional tests for fast behavioral feedback.
test-fast:
    FROM +python-workspace
    RUN uv run pytest tests/unit tests/contract tests/functional

# Run Fake resilience tests while excluding process-level Docker recovery tests.
test-resilience:
    FROM +python-workspace
    RUN uv run pytest -m "resilience and not docker_resilience" tests/resilience

# Run deterministic offline retrieval evaluation without the real E2E evaluation.
test-eval:
    FROM +python-workspace
    RUN uv run pytest -m "eval and not e2e" tests/eval

# Enforce 85 percent coverage across the four core Python packages using offline suites only.
test-coverage:
    FROM +python-workspace
    RUN uv run pytest --cov=rag_mvp.domain --cov=rag_mvp.application --cov=rag_mvp.ingestion --cov=rag_mvp.retrieval --cov-fail-under=85 -m "not e2e and not docker_resilience and not integration and not model_integration" tests/unit tests/contract tests/functional tests/resilience tests/eval

# Aggregate every offline behavioral, resilience, evaluation, and coverage target.
test:
    BUILD +test-fast
    BUILD +test-resilience
    BUILD +test-eval
    BUILD +test-coverage

# Run the complete Secret-free gate used by pre-commit and pull-request CI.
ci:
    BUILD +lint
    BUILD +test
