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
    assert settings.grpc_address == "127.0.0.1:50052"


def test_production_rejects_grpc_reflection() -> None:
    with pytest.raises(ValidationError, match="reflection"):
        Settings(
            _env_file=None,
            environment=Environment.PRODUCTION,
            grpc_reflection=True,
            mysql_dsn="mysql+asyncmy://rag:strong-password@mysql:3306/rag",
        )


def test_production_rejects_development_mysql_credentials() -> None:
    with pytest.raises(ValidationError, match="MySQL"):
        Settings(
            _env_file=None,
            environment=Environment.PRODUCTION,
            grpc_reflection=False,
        )


def test_test_markers_are_registered(request: pytest.FixtureRequest) -> None:
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
    ["sqlalchemy", "asyncmy", "alembic", "httpx", "elasticsearch", "nats"],
)
def test_runtime_adapter_dependencies_are_importable(module_name: str) -> None:
    assert importlib.util.find_spec(module_name) is not None
