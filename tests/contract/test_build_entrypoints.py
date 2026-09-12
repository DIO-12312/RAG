from __future__ import annotations

# 校验 Makefile 与 Earthfile 公开入口、密钥隔离及卷保护约束。
import json
import os
import re
import subprocess
from pathlib import Path

import yaml

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
    earthfile_targets = {
        "proto",
        "lint",
        "test",
        "ci",
        "docker-up",
        "docker-test",
        "docker-down",
        "run",
        "web-restart",
    }
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
    assert "COPY apps ./apps" in earthfile
    assert "COPY backend ./backend" in earthfile
    assert "compose.product.yml" in earthfile
    assert "compose.production.yml" in earthfile
    assert "COPY deploy ./deploy" in earthfile
    for aggregate in ("lint", "test", "ci"):
        assert f"\n{aggregate}:\n    FROM +python-workspace\n" in earthfile


def test_quality_workflow_runs_for_push_and_main_pull_requests() -> None:
    workflow = yaml.safe_load(_text(".github/workflows/quality.yml"))

    assert workflow["on"] == {
        "push": {"branches": ["**"]},
        "pull_request": {"branches": ["main"]},
        "workflow_dispatch": {},
    }
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"] == {
        "group": (
            "${{ github.workflow }}-${{ github.event_name }}-"
            "${{ github.event.pull_request.number || github.ref }}"
        ),
        "cancel-in-progress": True,
    }
    assert set(workflow["jobs"]) == {"quality"}
    job = workflow["jobs"]["quality"]
    assert job["name"] == "python-quality"
    assert job["runs-on"] == "ubuntu-24.04"
    assert 10 <= job["timeout-minutes"] <= 30
    assert "if" not in job
    assert not job.get("continue-on-error", False)
    steps = job["steps"]
    gate = next(step for step in steps if step.get("id") == "gate")
    assert gate["run"] == "make ci"
    assert "if" not in gate
    assert gate.get("env") == {"EARTHLY_FLAGS": "--ci"}
    assert all(not step.get("continue-on-error", False) for step in steps)


def test_quality_workflow_pins_tools_and_keeps_secrets_out() -> None:
    text = _text(".github/workflows/quality.yml")
    workflow = yaml.safe_load(text)
    job = workflow["jobs"]["quality"]
    assert workflow["defaults"]["run"]["shell"] == "bash"
    assert "permissions" not in job
    assert "environment" not in job
    assert "services" not in job
    actions = [step for step in job["steps"] if "uses" in step]
    assert len(actions) == 1
    assert re.fullmatch(r"actions/checkout@[0-9a-f]{40}", actions[0]["uses"])
    assert actions[0]["with"] == {"persist-credentials": False}
    install = next(step for step in job["steps"] if step.get("id") == "install")
    assert "releases/download/v0.8.16/earthly-linux-amd64" in install["run"]
    assert "sha256sum --check --strict" in install["run"]
    assert "--retry 3" in install["run"]
    assert "GITHUB_PATH" in install["run"]
    for forbidden in (
        "secrets.",
        "pull_request_target",
        "docker compose",
        "docker-test",
        "docker-down",
        "make all",
        "uv run",
        "pytest ",
        "--no-verify",
        "|| true",
    ):
        assert forbidden not in text
    earthfile = _text("Earthfile")
    assert "COPY .github/workflows/quality.yml ./.github/workflows/quality.yml" in earthfile
    assert "COPY .github/main-ruleset.json ./.github/main-ruleset.json" in earthfile


def test_main_ruleset_protects_main_without_blocking_direct_push() -> None:
    ruleset = json.loads(_text(".github/main-ruleset.json"))

    assert ruleset["target"] == "branch"
    assert ruleset["enforcement"] == "active"
    assert ruleset["bypass_actors"] == []
    assert ruleset["conditions"] == {"ref_name": {"include": ["refs/heads/main"], "exclude": []}}
    rules = {rule["type"]: rule for rule in ruleset["rules"]}
    assert len(rules) == len(ruleset["rules"]) == 2
    assert set(rules) == {"deletion", "non_fast_forward"}
    # 直接 push main 是约定工作方式；加入必需检查或 PR 规则会把检查变成合入拦截。
    assert "required_status_checks" not in rules
    assert "pull_request" not in rules


def test_docker_entrypoints_validate_suites_scan_logs_and_preserve_volumes() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    makefile = _text("Makefile")
    earthfile = _text("Earthfile")
    compose = _text("docker-compose.yml")
    public = {
        "all",
        "proto",
        "lint",
        "test",
        "ci",
        "docker-up",
        "docker-test",
        "docker-down",
        "run",
        "web-restart",
        "clear",
        "help",
    }

    assert _make_targets(makefile) == public
    assert re.search(r"^# .+\nrun:\n", makefile, re.MULTILINE)
    run_target = earthfile.split("\nrun:\n", 1)[1].split("\n#", 1)[0]
    assert "LOCALLY" in run_target
    steps = [
        "docker volume create rag-product_product-keys",
        "DO +DOCKER_START",
        "docker compose -f compose.product.yml up -d --build --wait --wait-timeout 240",
    ]
    positions = [run_target.index(step) for step in steps]
    assert positions == sorted(positions)
    assert "npm" not in run_target, "frontend must be containerized, not started via npm dev"
    product_compose = _text("compose.product.yml")
    assert "web:" in product_compose
    assert "apps/web" in product_compose
    assert "powershell" not in run_target.lower()
    assert "volume rm" not in run_target
    assert "SUITE ?= all" in makefile
    assert "EVAL_FIXTURE ?= rephrased" in makefile
    assert "+docker-test --SUITE=$(SUITE)" in makefile
    assert "--EVAL_FIXTURE=$(EVAL_FIXTURE)" in makefile
    assert re.search(r"^# .+\nDOCKER_START:\n\s+FUNCTION$", earthfile, re.MULTILINE)
    assert "ARG EVAL_FIXTURE=rephrased" in earthfile
    assert 'case "$EVAL_FIXTURE" in original|rephrased)' in earthfile
    assert '-e EVAL_FIXTURE="$EVAL_FIXTURE"' in earthfile
    assert earthfile.count("DO +DOCKER_START") == 3
    assert "docker-start:\n    FUNCTION" not in earthfile
    for target in ("docker-up", "docker-test", "docker-down"):
        assert re.search(rf"^# .+\n{re.escape(target)}:", makefile, re.MULTILINE)
        assert re.search(rf"^# .+\n{re.escape(target)}:", earthfile, re.MULTILINE)
    assert "LOCALLY" in earthfile
    assert "docker compose config --quiet" in earthfile
    assert (
        "docker compose --profile test build rag-security-materials elasticsearch "
        "rag-search-guard-bootstrap rag-migrate rag-server rag-worker rag-outbox rag-test"
        in earthfile
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


def test_docker_entrypoints_build_search_guard_and_pass_file_secret_paths() -> None:
    """真实 Docker 入口必须构建安全服务且只传递 ES secret 文件路径。"""

    earthfile = _text("Earthfile")

    assert "Dockerfile.elasticsearch" in _text("docker-compose.yml")
    assert "rag-security-materials" in earthfile
    assert "rag-search-guard-bootstrap" in earthfile
    assert "RAG_TEST_ELASTICSEARCH_PASSWORD_FILE=/run/secrets/rag_mvp_password" in earthfile
    assert "RAG_TEST_ELASTICSEARCH_CA_CERT=/run/secrets/ca.pem" in earthfile
    assert "docker compose config --quiet" in earthfile
    assert "docker compose config >" not in earthfile


def test_containerized_web_upload_limits_match_supported_rag_sources() -> None:
    """Web、Nginx 与 Go 上传入口必须共同接纳 32 MiB 的 CHM/CHI 等 RAG 文件。"""

    upload_panel = _text("apps/web/src/components/UploadPanel.vue")
    nginx = _text("apps/web/nginx.conf")
    resources = _text("backend/go-api/internal/httpapi/resources.go")

    expected_extensions = ("pdf", "md", "txt", "py", "go", "js", "ts", "java", "chm", "chi")
    for extension in expected_extensions:
        assert f".{extension}" in upload_panel
        assert f".{extension}" in resources
    assert "file.size > 32*1024*1024" in upload_panel
    assert "client_max_body_size 34m;" in nginx


def test_web_restart_only_rebuilds_web_through_earthly(tmp_path: Path) -> None:
    """Make delegates to Earthly; local Docker commands only recreate web."""
    recorder = tmp_path / "recorder"
    recorder.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n', encoding="utf-8")
    recorder.chmod(0o755)
    recipe = re.search(r"^web-restart:\n\t(.+)$", _text("Makefile"), re.MULTILINE)
    assert recipe is not None
    # Run the actual recipe with Make variables resolved; offline images need no GNU Make.
    command = recipe.group(1).replace("$(EARTHLY)", str(recorder))
    command = command.replace("$(EARTHLY_ENV_FILE)", ".earthly.env")
    command = command.replace("$(EARTHLY_FLAGS)", "")
    result = subprocess.run(
        ["sh", "-c", command],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.splitlines() == ["--env-file-path", ".earthly.env", "+web-restart"]
    earthfile = _text("Earthfile")
    target = earthfile.split("\nweb-restart:\n", 1)[1].split("\n#", 1)[0]
    assert "    LOCALLY" in target
    assert "DO +DOCKER_START" not in target
    docker = tmp_path / "docker"
    docker.write_text(recorder.read_text(encoding="utf-8"), encoding="utf-8")
    docker.chmod(0o755)
    commands = re.findall(r"^    RUN (.+)$", target, re.MULTILINE)
    calls = []
    for command in commands:
        completed = subprocess.run(
            ["sh", "-c", command],
            cwd=ROOT,
            env={**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}"},
            capture_output=True,
            text=True,
            check=True,
        )
        calls.append(completed.stdout.splitlines())
    assert calls == [
        ["compose", "-f", "compose.product.yml", "config", "--quiet"],
        [
            "compose",
            "-f",
            "compose.product.yml",
            "up",
            "-d",
            "--build",
            "--no-deps",
            "--force-recreate",
            "--wait",
            "--wait-timeout",
            "240",
            "web",
        ],
    ]
