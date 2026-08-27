from __future__ import annotations

# 校验 Makefile 与 Earthfile 公开入口、密钥隔离及卷保护约束。
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _text(path: str) -> str:
    """构造本测试所需的输入、替身或运行环境。"""
    return (ROOT / path).read_text(encoding="utf-8")


def _make_targets(makefile: str) -> set[str]:
    """构造本测试所需的输入、替身或运行环境。"""
    return set(re.findall(r"^([a-z][a-z0-9-]*):(?:\s|$)", makefile, re.MULTILINE))


def test_makefile_offline_targets_are_commented_earthly_only_entrypoints() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    makefile = _text("Makefile")
    expected = {"proto", "lint", "test", "ci", "help"}

    assert expected <= _make_targets(makefile)
    assert "EARTHLY ?= earthly" in makefile
    assert "EARTHLY_ENV_FILE ?= .earthly.env" in makefile
    assert "EARTHLY_FLAGS ?=" in makefile
    assert (ROOT / ".earthly.env").read_text(encoding="utf-8").startswith("# Intentionally empty")
    earthfile_targets = {"proto", "lint", "test", "ci", "docker-up", "docker-test", "docker-down"}
    execution_recipes = [
        match.group("recipe")
        for match in re.finditer(
            rf"^(?:{'|'.join(sorted(earthfile_targets))}):\n(?P<recipe>\t.+)$",
            makefile,
            re.MULTILINE,
        )
    ]
    assert execution_recipes
    assert all(
        "$(EARTHLY) --env-file-path $(EARTHLY_ENV_FILE) $(EARTHLY_FLAGS)" in line
        for line in execution_recipes
    )
    for target in expected:
        assert re.search(rf"^# .+\n{re.escape(target)}:", makefile, re.MULTILINE)
    for target in {"proto", "lint", "test", "ci"}:
        assert f"+{target}" in makefile


def test_earthfile_pins_tools_and_separates_offline_targets() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    earthfile = _text("Earthfile")

    assert earthfile.startswith("VERSION --no-implicit-ignore --use-function-keyword 0.8\n")
    preamble = earthfile.split("# Export the pinned uv binary", maxsplit=1)[0]
    assert "\nFROM " not in preamble
    assert "ghcr.io/astral-sh/uv:0.12.1" in earthfile
    assert "python:3.12.11-slim-bookworm" in earthfile
    for target in (
        "proto",
        "proto-check",
        "ruff-check",
        "format-check",
        "type-check",
        "lint",
        "test-fast",
        "test-resilience",
        "test-eval",
        "test-coverage",
        "test",
        "ci",
    ):
        assert re.search(rf"^# .+\n{re.escape(target)}:", earthfile, re.MULTILINE)
    assert "scripts/generate_proto.py" in earthfile
    assert "scripts/check_generated.py" in earthfile
    assert "generated/* AS LOCAL" not in earthfile
    for generated_file in (
        "__init__.py",
        "rag_service_pb2.py",
        "rag_service_pb2.pyi",
        "rag_service_pb2_grpc.py",
    ):
        generated_path = f"src/rag_mvp/rpc/generated/{generated_file}"
        assert f"SAVE ARTIFACT {generated_path} AS LOCAL {generated_path}" in earthfile
    assert "--cov-fail-under=85" in earthfile
    assert "resilience and not docker_resilience" in earthfile
    assert "eval and not e2e" in earthfile
    assert "EMBEDDING_MODEL_API_KEY" not in earthfile


def test_docker_entrypoints_validate_suites_scan_logs_and_preserve_volumes() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    makefile = _text("Makefile")
    earthfile = _text("Earthfile")
    compose = _text("docker-compose.yml")
    public = {
        "proto",
        "lint",
        "test",
        "ci",
        "docker-up",
        "docker-test",
        "docker-down",
        "clear",
        "help",
    }

    assert _make_targets(makefile) == public
    assert "SUITE ?= integration" in makefile
    assert "EVAL_FIXTURE ?= rephrased" in makefile
    assert "+docker-test --SUITE=$(SUITE)" in makefile
    assert "--EVAL_FIXTURE=$(EVAL_FIXTURE)" in makefile
    assert re.search(r"^# .+\nDOCKER_START:\n\s+FUNCTION$", earthfile, re.MULTILINE)
    assert "ARG EVAL_FIXTURE=rephrased" in earthfile
    assert 'case "$EVAL_FIXTURE" in original|rephrased)' in earthfile
    assert '-e EVAL_FIXTURE="$EVAL_FIXTURE"' in earthfile
    assert earthfile.count("DO +DOCKER_START") == 2
    assert "docker-start:\n    FUNCTION" not in earthfile
    for target in ("docker-up", "docker-test", "docker-down"):
        assert re.search(rf"^# .+\n{re.escape(target)}:", makefile, re.MULTILINE)
        assert re.search(rf"^# .+\n{re.escape(target)}:", earthfile, re.MULTILINE)
    assert "LOCALLY" in earthfile
    assert "docker compose config --quiet" in earthfile
    assert (
        "docker compose --profile test build rag-migrate rag-server rag-worker "
        "rag-outbox rag-test" in earthfile
    )
    for suite in ("integration", "resilience", "eval", "all"):
        assert f"{suite})" in earthfile
    eval_command = next(line for line in earthfile.splitlines() if "run_eval()" in line)
    assert "tests/eval/test_real_retrieval_quality.py" in eval_command
    assert "tests/eval/test_real_computer_architecture_pdf_quality.py" in eval_command
    assert '--user "$(id -u):$(id -g)"' in eval_command
    assert "Unknown SUITE:" in earthfile
    assert "scripts/check_secret_leaks.py" in earthfile
    assert "docker compose down --remove-orphans" in earthfile
    assert "down -v" not in earthfile
    assert "./tests/eval/log:/app/tests/eval/log:rw" in compose
