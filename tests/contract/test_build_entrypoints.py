from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _make_targets(makefile: str) -> set[str]:
    return set(re.findall(r"^([a-z][a-z0-9-]*):(?:\s|$)", makefile, re.MULTILINE))


def test_makefile_offline_targets_are_commented_earthly_only_entrypoints() -> None:
    makefile = _text("Makefile")
    expected = {"proto", "lint", "test", "ci", "help"}

    assert expected <= _make_targets(makefile)
    assert "EARTHLY ?= earthly" in makefile
    assert "EARTHLY_ENV_FILE ?= .earthly.env" in makefile
    assert "EARTHLY_FLAGS ?=" in makefile
    assert (ROOT / ".earthly.env").read_text(encoding="utf-8").startswith("# Intentionally empty")
    execution_recipes = [
        line
        for line in makefile.splitlines()
        if line.startswith("\t") and not line.lstrip().startswith("@echo")
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
    makefile = _text("Makefile")
    earthfile = _text("Earthfile")
    public = {
        "proto",
        "lint",
        "test",
        "ci",
        "docker-up",
        "docker-test",
        "docker-down",
        "help",
    }

    assert _make_targets(makefile) == public
    assert "SUITE ?= integration" in makefile
    assert "+docker-test --SUITE=$(SUITE)" in makefile
    assert re.search(r"^# .+\nDOCKER_START:\n\s+FUNCTION$", earthfile, re.MULTILINE)
    assert earthfile.count("DO +DOCKER_START") == 2
    assert "docker-start:\n    FUNCTION" not in earthfile
    for target in ("docker-up", "docker-test", "docker-down"):
        assert re.search(rf"^# .+\n{re.escape(target)}:", makefile, re.MULTILINE)
        assert re.search(rf"^# .+\n{re.escape(target)}:", earthfile, re.MULTILINE)
    assert "LOCALLY" in earthfile
    assert "docker compose config --quiet" in earthfile
    for suite in ("integration", "resilience", "eval", "all"):
        assert f"{suite})" in earthfile
    eval_command = next(line for line in earthfile.splitlines() if "run_eval()" in line)
    assert "tests/eval/test_real_retrieval_quality.py" in eval_command
    assert "tests/eval/test_real_computer_architecture_pdf_quality.py" in eval_command
    assert "Unknown SUITE:" in earthfile
    assert "scripts/check_secret_leaks.py" in earthfile
    assert "docker compose down --remove-orphans" in earthfile
    assert "down -v" not in earthfile


def test_hook_and_quick_workflow_delegate_only_to_make_ci() -> None:
    hook_lines = [
        line.strip() for line in _text(".githooks/pre-commit").splitlines() if line.strip()
    ]
    workflow = _text(".github/workflows/quality.yml")

    assert hook_lines == ["#!/bin/sh", "set -eu", "make ci"]
    assert "earthly/actions-setup@v1" in workflow
    assert 'version: "v0.8.16"' in workflow
    assert "EARTHLY_FLAGS: --ci" in workflow
    assert "run: make ci" in workflow
    assert "setup-python" not in workflow
    assert "uv run" not in workflow
    assert "pytest" not in workflow
    assert "ruff" not in workflow
    assert "mypy" not in workflow
    assert "secrets." not in workflow


def test_docker_workflow_delegates_real_suites_and_always_cleans_up() -> None:
    workflow = _text(".github/workflows/docker-quality.yml")

    assert workflow.count("earthly/actions-setup@v1") == 2
    assert workflow.count('version: "v0.8.16"') == 2
    assert workflow.count("EARTHLY_FLAGS: --ci") == 2
    assert "make docker-test SUITE=integration" in workflow
    assert "make docker-test SUITE=resilience" in workflow
    assert "make docker-test SUITE=eval" in workflow
    assert workflow.count("make docker-down") == 2
    assert workflow.count("if: always()") == 2
    assert "docker compose" not in workflow
    assert "uv run pytest" not in workflow
    assert "pull_request_target:" not in workflow
    assert "EMBEDDING_MODEL_API_KEY: ${{ secrets.EMBEDDING_MODEL_API_KEY }}" in workflow
