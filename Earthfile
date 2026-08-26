VERSION --no-implicit-ignore --use-function-keyword 0.8

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
    COPY README.md ./README.md
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
    COPY .dockerignore .gitattributes .earthly.env ./
    RUN uv sync --frozen --group dev

# Regenerate protobuf code, verify it, and export only successful generated files to the host.
proto:
    FROM +python-workspace
    RUN uv run python scripts/generate_proto.py
    RUN uv run python scripts/check_generated.py
    SAVE ARTIFACT src/rag_mvp/rpc/generated/__init__.py AS LOCAL src/rag_mvp/rpc/generated/__init__.py
    SAVE ARTIFACT src/rag_mvp/rpc/generated/rag_service_pb2.py AS LOCAL src/rag_mvp/rpc/generated/rag_service_pb2.py
    SAVE ARTIFACT src/rag_mvp/rpc/generated/rag_service_pb2.pyi AS LOCAL src/rag_mvp/rpc/generated/rag_service_pb2.pyi
    SAVE ARTIFACT src/rag_mvp/rpc/generated/rag_service_pb2_grpc.py AS LOCAL src/rag_mvp/rpc/generated/rag_service_pb2_grpc.py

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

# Validate, build, and start the complete Compose topology; requires Docker and a valid .env.
DOCKER_START:
    FUNCTION
    RUN docker compose config --quiet
    RUN docker compose --profile test build rag-server rag-worker rag-outbox rag-test
    RUN docker compose up -d --wait --wait-timeout 240 rag-server rag-worker rag-outbox

# Start the complete RAG service topology and wait for every declared health condition.
docker-up:
    LOCALLY
    DO +DOCKER_START

# Run a selected real Docker suite and preserve the service state for diagnosis after failure.
docker-test:
    LOCALLY
    ARG SUITE=integration
    ARG EVAL_FIXTURE=rephrased
    RUN case "$SUITE" in integration|resilience|eval|all) ;; *) echo "Unknown SUITE: $SUITE" >&2; exit 2 ;; esac
    RUN case "$EVAL_FIXTURE" in original|rephrased) ;; *) echo "Unknown EVAL_FIXTURE: $EVAL_FIXTURE" >&2; exit 2 ;; esac
    DO +DOCKER_START
    RUN run_integration() { docker compose --profile test run --rm -e RAG_MIGRATIONS_ROOT=/app -e RAG_TEST_MYSQL_DSN=mysql+asyncmy://rag:rag@mysql:3306/rag -e RAG_TEST_ELASTICSEARCH_URL=http://elasticsearch:9200 -e RAG_TEST_NATS_URL=nats://nats:4222 rag-test uv run pytest -m "integration or model_integration or e2e" tests/integration tests/e2e -q; }; \
        run_resilience() { docker compose -f docker-compose.yml -f tests/resilience/docker/docker-compose.resilience.yml config --quiet && docker compose -f docker-compose.yml -f tests/resilience/docker/docker-compose.resilience.yml --profile test build rag-server rag-worker rag-outbox rag-test && docker compose -f docker-compose.yml -f tests/resilience/docker/docker-compose.resilience.yml --profile test run --rm rag-test uv run pytest -m docker_resilience tests/resilience/docker -q; }; \
        run_eval() { docker compose --profile test run --rm --user "$(id -u):$(id -g)" -e EVAL_FIXTURE="$EVAL_FIXTURE" rag-test uv run pytest -m eval tests/eval/test_real_retrieval_quality.py tests/eval/test_real_computer_architecture_pdf_quality.py -q; }; \
        case "$SUITE" in integration) run_integration ;; resilience) run_resilience ;; eval) run_eval ;; all) run_integration && run_resilience && run_eval ;; esac

# Scan Compose logs for the configured API key, then stop services without deleting volumes.
docker-down:
    LOCALLY
    RUN log_file="$(mktemp)"; trap 'rm -f "$log_file"' EXIT; \
        docker compose logs --no-color >"$log_file" 2>&1 || true; \
        scan_status=0; \
        docker compose --profile test run --rm -T --no-deps rag-test uv run python scripts/check_secret_leaks.py <"$log_file" || scan_status=$?; \
        down_status=0; docker compose down --remove-orphans || down_status=$?; \
        if [ "$scan_status" -ne 0 ]; then exit "$scan_status"; fi; exit "$down_status"
