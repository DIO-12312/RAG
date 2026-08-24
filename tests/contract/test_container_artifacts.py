from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.docker_healthcheck import (
    RUNNING_SERVICES,
    _compose_process_output,
    compose_is_healthy,
    parse_compose_processes,
)

ROOT = Path(__file__).resolve().parents[2]


def _service_block(compose: str, service: str) -> str:
    marker = f"  {service}:\n"
    start = compose.index(marker)
    lines = compose[start:].splitlines()
    block = [lines[0]]
    for line in lines[1:]:
        if line and not line.startswith(" "):
            break
        if line.startswith("  ") and not line.startswith("    "):
            break
        block.append(line)
    return "\n".join(block)


def test_runtime_image_and_context_exclude_secrets_and_test_artifacts() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    ignored = {
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }

    assert "AS runtime" in dockerfile
    assert "AS test" in dockerfile
    assert "UV_CACHE_DIR=/tmp/uv-cache" in dockerfile
    assert "USER rag" in dockerfile
    assert "COPY tests" not in dockerfile
    assert "migrations /app/migrations" in dockerfile
    assert {".env", ".env.*", ".git/", "tests/", "data/", "logs/"} <= ignored


def test_compose_declares_migration_health_role_secrets_and_shared_storage() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    blocks = {
        service: _service_block(compose, service)
        for service in (
            "rag-migrate",
            "rag-server",
            "rag-worker",
            "rag-outbox",
            "rag-test",
        )
    }

    assert 'command: ["rag-migrate", "upgrade", "head"]' in blocks["rag-migrate"]
    assert "RAG_MIGRATIONS_ROOT: /app" in blocks["rag-migrate"]
    assert "condition: service_healthy" in blocks["rag-migrate"]
    for role in ("rag-server", "rag-worker", "rag-outbox"):
        assert "target: runtime" in blocks[role]
        assert "rag-migrate:" in blocks[role]
        assert "condition: service_completed_successfully" in blocks[role]
        assert "object-data:/app/data/objects" in blocks[role]
        assert "healthcheck:" in blocks[role]

    assert "target: test" in blocks["rag-test"]
    assert 'profiles: ["test"]' in blocks["rag-test"]
    assert "./tests:/app/tests:ro" in blocks["rag-test"]

    for role in ("rag-server", "rag-worker", "rag-test"):
        assert "EMBEDDING_MODEL_API_KEY" in blocks[role]
        assert "EMBEDDING_MODEL_DIMENSION" in blocks[role]
    for role in ("rag-migrate", "rag-outbox"):
        assert "EMBEDDING_MODEL_API_KEY" not in blocks[role]
        assert "EMBEDDING_MODEL_URL" not in blocks[role]


def test_secret_scanner_fails_without_echoing_the_secret() -> None:
    secret = "contract-secret-sentinel"
    environment = os.environ.copy()
    environment["EMBEDDING_MODEL_API_KEY"] = secret

    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_secret_leaks.py")],
        input=f"safe line\naccidental={secret}\n",
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )

    assert completed.returncode == 1
    assert completed.stdout.strip() == "secret leak detected"
    assert secret not in completed.stdout
    assert secret not in completed.stderr


def test_healthcheck_parses_ndjson_and_requires_every_process_to_be_healthy() -> None:
    records = {
        service: {"Service": service, "State": "running", "Health": "healthy", "ExitCode": 0}
        for service in RUNNING_SERVICES
    }
    records["rag-migrate"] = {
        "Service": "rag-migrate",
        "State": "exited",
        "Health": "",
        "ExitCode": 0,
    }
    output = "\n".join(json.dumps(record) for record in records.values())

    parsed = parse_compose_processes(output)

    assert compose_is_healthy(parsed) is True
    parsed["rag-worker"]["Health"] = "unhealthy"
    assert compose_is_healthy(parsed) is False


def test_healthcheck_decodes_docker_output_as_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        del args
        captured.update(kwargs)
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    _compose_process_output()

    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "replace"


def test_quality_workflows_keep_offline_and_secret_backed_suites_separate() -> None:
    quick = (ROOT / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
    docker_path = ROOT / ".github" / "workflows" / "docker-quality.yml"
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")

    assert docker_path.is_file()
    docker = docker_path.read_text(encoding="utf-8")
    assert "pull_request:" in quick
    assert "eval and not e2e" in quick
    assert "secrets." not in quick
    assert "workflow_dispatch:" in docker
    assert "schedule:" in docker
    assert "pull_request_target:" not in docker
    assert "EMBEDDING_MODEL_API_KEY: ${{ secrets.EMBEDDING_MODEL_API_KEY }}" in docker
    assert docker.count("docker compose config --quiet") >= 2
    assert "RAG_MIGRATIONS_ROOT=/app" in docker
    assert "tests/integration tests/e2e" in docker
    assert "tests/resilience/docker" in docker
    assert "tests/eval/test_real_retrieval_quality.py" in docker
    assert "down -v" not in docker
    assert ".githooks/* text eol=lf" in attributes
    assert ".github/workflows/*.yml text eol=lf" in attributes
