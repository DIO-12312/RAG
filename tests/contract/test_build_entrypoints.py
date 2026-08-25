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
    assert "EARTHLY_FLAGS ?=" in makefile
    execution_recipes = [
        line
        for line in makefile.splitlines()
        if line.startswith("\t") and not line.lstrip().startswith("@echo")
    ]
    assert execution_recipes
    assert all("$(EARTHLY) $(EARTHLY_FLAGS)" in line for line in execution_recipes)
    for target in expected:
        assert re.search(rf"^# .+\n{re.escape(target)}:", makefile, re.MULTILINE)
    for target in {"proto", "lint", "test", "ci"}:
        assert f"+{target}" in makefile


def test_earthfile_pins_tools_and_separates_offline_targets() -> None:
    earthfile = _text("Earthfile")

    assert earthfile.startswith("VERSION --no-implicit-ignore --use-function-keyword 0.8\n")
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
    assert "--cov-fail-under=85" in earthfile
    assert "resilience and not docker_resilience" in earthfile
    assert "eval and not e2e" in earthfile
    assert "EMBEDDING_MODEL_API_KEY" not in earthfile
