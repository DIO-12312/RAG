from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from rag_mvp import __version__
from rag_mvp.config import Environment, Settings


def test_package_import_has_no_runtime_side_effects() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    script = """
import sys

forbidden = []

def audit(event, args):
    if event.startswith(("socket.", "subprocess.", "threading.")):
        forbidden.append(event)

sys.addaudithook(audit)
import rag_mvp
assert rag_mvp.__version__ == "0.1.0"
assert forbidden == [], forbidden
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert __version__ == "0.1.0"


def test_settings_can_be_constructed_explicitly_for_tests(tmp_path: Path) -> None:
    """验证本测试场景的预期行为与边界条件。"""
    settings = Settings(
        _env_file=None,
        environment=Environment.TEST,
        grpc_host="127.0.0.1",
        grpc_port=50052,
        mysql_dsn="mysql+asyncmy://test:test@127.0.0.1:3306/rag_test",
        elasticsearch_url="http://127.0.0.1:9200",
        nats_url="nats://127.0.0.1:4222",
        object_root=tmp_path,
    )

    assert settings.environment is Environment.TEST
    assert settings.object_root == tmp_path
    assert settings.migrations_root == Path(".")
    assert settings.grpc_address == "127.0.0.1:50052"


def test_settings_builds_a_normalized_secret_embedding_profile() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    api_key = "integration-secret-value"
    settings = Settings(
        _env_file=None,
        embedding_model_url="https://model.example/v1/",
        embedding_model_name="embedding-model",
        embedding_model_api_key=api_key,
        embedding_model_dimension=1024,
    )

    profile = settings.require_embedding_profile()

    assert profile.endpoint == "https://model.example/v1/embeddings"
    assert profile.model == "embedding-model"
    assert profile.dimension == 1024
    assert profile.api_key.get_secret_value() == api_key
    assert api_key not in repr(settings)
    assert api_key not in repr(profile)


def test_elasticsearch_profile_reads_file_secret_without_repr_leak(tmp_path: Path) -> None:
    """ES 密码文件只在装配时读取，且 Secret 不得进入对象表示。"""

    password_file = tmp_path / "rag_mvp_password"
    password_file.write_text("test-es-password\n", encoding="utf-8")
    settings = Settings(
        _env_file=None,
        elasticsearch_url="https://elasticsearch:9200",
        elasticsearch_username="rag_mvp",
        elasticsearch_password_file=password_file,
        elasticsearch_ca_cert=tmp_path / "ca.pem",
    )

    profile = settings.require_elasticsearch_profile()

    assert profile.password.get_secret_value() == "test-es-password"
    assert "test-es-password" not in repr(settings)
    assert "test-es-password" not in repr(profile)


@pytest.mark.parametrize("url", ["http://elasticsearch:9200", "https://"])
def test_elasticsearch_profile_rejects_insecure_or_invalid_endpoint(url: str) -> None:
    """HTTP 或不完整 endpoint 不得绕过 Elasticsearch TLS 边界。"""

    with pytest.raises(ValueError, match="Elasticsearch"):
        Settings(_env_file=None, elasticsearch_url=url).require_elasticsearch_profile()


def test_elasticsearch_profile_rejects_multiple_secret_sources(tmp_path: Path) -> None:
    """同一运行角色必须从唯一密码来源读取，避免配置歧义。"""

    password_file = tmp_path / "rag_mvp_password"
    password_file.write_text("file-secret\n", encoding="utf-8")

    with pytest.raises(ValueError, match="password"):
        Settings(
            _env_file=None,
            elasticsearch_url="https://elasticsearch:9200",
            elasticsearch_password="environment-secret",
            elasticsearch_password_file=password_file,
            elasticsearch_ca_cert=tmp_path / "ca.pem",
        ).require_elasticsearch_profile()


def test_embedding_profile_rejects_missing_or_partial_configuration() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    with pytest.raises(ValueError, match="embedding model configuration"):
        Settings(_env_file=None).require_embedding_profile()

    with pytest.raises(ValueError, match="embedding model configuration"):
        Settings(
            _env_file=None,
            embedding_model_url="https://model.example/v1",
        ).require_embedding_profile()


@pytest.mark.parametrize(
    "overrides,error",
    [
        ({"parser_version": " "}, "parser_version"),
        ({"chunk_size": 100, "chunk_overlap": 100}, "chunk_overlap"),
        ({"chunk_size": 100, "chunk_overlap": 101}, "chunk_overlap"),
    ],
)
def test_parser_and_chunk_runtime_settings_reject_inconsistent_values(
    overrides: dict[str, object],
    error: str,
) -> None:
    """验证本测试场景的预期行为与边界条件。"""
    with pytest.raises(ValidationError, match=error):
        Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def test_production_rejects_grpc_reflection() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    with pytest.raises(ValidationError, match="reflection"):
        Settings(
            _env_file=None,
            environment=Environment.PRODUCTION,
            grpc_reflection=True,
            mysql_dsn="mysql+asyncmy://rag:strong-password@mysql:3306/rag",
        )


def test_production_rejects_development_mysql_credentials() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    with pytest.raises(ValidationError, match="MySQL"):
        Settings(
            _env_file=None,
            environment=Environment.PRODUCTION,
            grpc_reflection=False,
        )


def test_test_markers_are_registered(request: pytest.FixtureRequest) -> None:
    """验证本测试场景的预期行为与边界条件。"""
    markers = "\n".join(request.config.getini("markers"))

    for marker in (
        "integration",
        "model_integration",
        "e2e",
        "resilience",
        "docker_resilience",
        "eval",
    ):
        assert f"{marker}:" in markers


@pytest.mark.parametrize(
    "module_name",
    ["sqlalchemy", "asyncmy", "cryptography", "alembic", "httpx", "elasticsearch", "nats"],
)
def test_runtime_adapter_dependencies_are_importable(module_name: str) -> None:
    """验证本测试场景的预期行为与边界条件。"""
    assert importlib.util.find_spec(module_name) is not None
