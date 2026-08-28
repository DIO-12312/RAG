from __future__ import annotations

# 校验容器构建产物包含运行所需代码且不泄露开发期文件。
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


def test_package_and_container_use_canonical_root_readme() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    readme = ROOT / "README.md"
    legacy_readme = ROOT / "docs" / "README.md"
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    earthfile = (ROOT / "Earthfile").read_text(encoding="utf-8")

    assert readme.is_file()
    assert not legacy_readme.exists()
    assert 'readme = "README.md"' in pyproject
    assert "docs/README.md" not in pyproject
    assert "COPY README.md ./README.md" in dockerfile
    assert "COPY --chown=rag:rag README.md ./README.md" in dockerfile
    assert "docs/README.md" not in dockerfile
    assert "COPY README.md ./README.md" in earthfile
    assert "docs/README.md" not in earthfile


def _service_block(compose: str, service: str) -> str:
    """构造本测试所需的输入、替身或运行环境。"""
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
    """验证本测试场景的预期行为与边界条件。"""
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
    """验证本测试场景的预期行为与边界条件。"""
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


def test_compose_keeps_infrastructure_private_and_orders_search_guard_bootstrap() -> None:
    """安全启动前不能暴露中间件，且下游必须等待 Search Guard 初始化。"""

    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    elasticsearch = _service_block(compose, "elasticsearch")
    bootstrap = _service_block(compose, "rag-search-guard-bootstrap")
    outbox = _service_block(compose, "rag-outbox")

    for public_port in ('"9200:9200"', '"3306:3306"', '"4222:4222"', '"8222:8222"'):
        assert public_port not in compose
    assert "rag-security-materials:" in elasticsearch
    assert "condition: service_completed_successfully" in elasticsearch
    assert "elasticsearch:" in bootstrap
    assert "condition: service_started" in bootstrap
    assert "RAG_ELASTICSEARCH_PASSWORD_FILE" not in outbox


def test_debug_override_binds_elasticsearch_to_loopback_only() -> None:
    """排障 override 只能将已认证 HTTPS ES 绑定到本机回环地址。"""

    debug_compose = (ROOT / "docker-compose.debug.yml").read_text(encoding="utf-8")

    assert '"127.0.0.1:9200:9200"' in debug_compose
    assert '"9200:9200"' not in debug_compose.replace('"127.0.0.1:9200:9200"', "")


def test_secret_scanner_fails_without_echoing_the_secret() -> None:
    """验证本测试场景的预期行为与边界条件。"""
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


def test_secret_scanner_detects_elasticsearch_password_without_echoing_it(
    tmp_path: Path,
) -> None:
    """ES password file 命中日志时扫描器必须失败且不得二次泄漏。"""

    secret = "elasticsearch-secret-sentinel"
    password_file = tmp_path / "rag_mvp_password"
    password_file.write_text(f"{secret}\n", encoding="utf-8")
    environment = os.environ.copy()
    environment.update(
        {
            "EMBEDDING_MODEL_API_KEY": "embedding-secret-sentinel",
            "RAG_ELASTICSEARCH_URL": "https://elasticsearch:9200",
            "RAG_ELASTICSEARCH_USERNAME": "rag_mvp",
            "RAG_ELASTICSEARCH_PASSWORD_FILE": str(password_file),
            "RAG_ELASTICSEARCH_CA_CERT": str(tmp_path / "ca.pem"),
        }
    )

    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_secret_leaks.py")],
        input=f"Authorization: Basic {secret}\n",
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
    """验证本测试场景的预期行为与边界条件。"""
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
    """验证本测试场景的预期行为与边界条件。"""
    captured: dict[str, object] = {}

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        """执行测试所需的辅助操作。"""
        del args
        captured.update(kwargs)
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    _compose_process_output()

    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "replace"
