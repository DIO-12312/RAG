# Earthfile 与 Makefile 统一构建入口实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 Earthfile 集中维护 protobuf、质量检查、离线测试和 Docker Compose 真实测试命令，并用八个带注释的 Makefile target 为开发者、Git Hook 和 GitHub Actions 提供统一入口。

**Architecture:** Python 生成、Lint、类型检查和离线 pytest 在 Earthly 管理的 Python 3.12 Linux 环境运行；Docker Compose 启停与真实测试在带 `LOCALLY` 的 Earthly target 中调用宿主机 Docker。Makefile 只负责稳定别名、帮助和 `SUITE` 参数转发，Hook、CI 与文档不得复制底层命令。

**Tech Stack:** Earthly 0.8.16、Earthfile 0.8、GNU Make、Python 3.12.11、uv 0.12.1、Ruff、mypy、pytest、Docker Compose、GitHub Actions

**Spec:** `docs/superpowers/specs/2026-08-25-build-command-orchestration-design.md`

## Global Constraints

- 公开 Make target 只能是 `proto`、`lint`、`test`、`ci`、`docker-up`、`docker-test`、`docker-down` 和 `help`。
- 每个 Make target、Earthly target 和 Earthly function 上方必须有说明用途、副作用或依赖的注释。
- `Makefile` 不得直接包含 `uv run`、`pytest`、`ruff`、`mypy` 或 `docker compose`；完整命令只存在于 `Earthfile`。
- Python 离线 target 固定使用 Python 3.12.11 与 uv 0.12.1，不读取 `.env` 或真实 Embedding 配置。
- `SUITE` 默认是 `integration`，只允许 `integration`、`resilience`、`eval`、`all`；非法值必须在运行测试前失败。
- Compose 配置只能执行 `docker compose config --quiet`，禁止输出 Secret 展开后的配置。
- 所有 down 操作禁止 `-v`，必须保留 MySQL、Elasticsearch、NATS 和对象存储持久卷。
- `docker-test` 失败后保留现场；GitHub Actions 通过 `if: always()` 调用 `make docker-down`。
- Git Hook 与无 Secret 的 GitHub quick job 只运行 `make ci`，不得调用真实模型、真实基础设施或 Docker resilience。
- 新增或修改测试文件、测试函数及其职责时，在同一提交同步更新 `tests/TEST.md`。
- 每个任务提交前只暂存该任务拥有的文件；现有 `.env.example` 修改属于外部工作，必须保持未暂存。
- 每个任务完成后更新本计划对应复选框，并使用 Conventional Commits 独立提交。

---

## File Structure

| 文件 | 单一职责 |
|---|---|
| `Earthfile` | 完整执行命令、共享 Python 构建环境、内部依赖图和 Docker suite 分派 |
| `Makefile` | 八个公共别名、`EARTHLY`/`EARTHLY_FLAGS`/`SUITE` 参数转发及帮助 |
| `.gitattributes` | 固定 Earthfile、Makefile、Hook 与 workflow 的 LF 行尾 |
| `tests/contract/test_build_entrypoints.py` | 验证公共接口、注释、隔离边界、危险参数和 CI/Hook 对齐 |
| `tests/contract/test_container_artifacts.py` | 保留容器、Secret 和真实/离线工作流分离的既有契约 |
| `.githooks/pre-commit` | 只调用统一快速门禁 `make ci` |
| `.github/workflows/quality.yml` | 安装 Earthly 并调用 `make ci` |
| `.github/workflows/docker-quality.yml` | 校验 Secret，调用参数化真实 suite，并保证 always cleanup |
| `tests/TEST.md` | 登记新增契约测试的目录和函数职责 |
| `docs/SPEC.md` | 固化统一构建入口、工具版本、测试与 Hook 约束 |
| `docs/testing-guide.md` | 说明高层 Make 入口、suite 参数和底层单项排错命令 |
| `docs/README.md` | 在完整 README 重写前先把散落长命令收敛为 Make 入口 |
| `AGENTS.md` | 要求后续改动优先运行统一入口并如实报告未运行的真实 suite |

---

### Task 1: 离线 Earthly 执行图与 Makefile 公共入口

**Files:**
- Create: `Earthfile`
- Create: `Makefile`
- Create: `tests/contract/test_build_entrypoints.py`
- Modify: `.gitattributes`
- Modify: `tests/TEST.md`
- Modify: `docs/superpowers/plans/2026-08-25-build-command-orchestration.md`

**Interfaces:**
- Consumes: `pyproject.toml`、`uv.lock`、`scripts/generate_proto.py`、`scripts/check_generated.py` 和既有 pytest markers。
- Produces: Earthly targets `+proto`、`+proto-check`、`+ruff-check`、`+format-check`、`+type-check`、`+lint`、`+test-fast`、`+test-resilience`、`+test-eval`、`+test-coverage`、`+test`、`+ci`；Make targets `proto`、`lint`、`test`、`ci`、`help`。

- [x] **Step 1: 写入离线入口的失败契约测试**

在 `tests/contract/test_build_entrypoints.py` 写入：

```python
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
    assert "uv run" not in makefile
    assert "pytest" not in makefile
    assert "ruff" not in makefile
    assert "mypy" not in makefile
    assert "docker compose" not in makefile
    for target in expected:
        assert re.search(rf"^# .+\n{re.escape(target)}:", makefile, re.MULTILINE)
    for target in {"proto", "lint", "test", "ci"}:
        assert f"+{target}" in makefile


def test_earthfile_pins_tools_and_separates_offline_targets() -> None:
    earthfile = _text("Earthfile")

    assert earthfile.startswith(
        "VERSION --no-implicit-ignore --use-function-keyword 0.8\n"
    )
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
    assert 'resilience and not docker_resilience' in earthfile
    assert 'eval and not e2e' in earthfile
    assert "EMBEDDING_MODEL_API_KEY" not in earthfile
```

- [x] **Step 2: 运行测试并确认因入口文件不存在而失败**

Run: `uv run pytest tests/contract/test_build_entrypoints.py -q`

Expected: FAIL，错误明确指出 `Makefile` 或 `Earthfile` 不存在。

- [x] **Step 3: 实现固定 Python 环境和离线 Earthly targets**

在 `Earthfile` 使用以下结构，完整 pytest 参数必须从现有 Hook/CI 原样迁移：

```Earthfile
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
```

- [x] **Step 4: 实现五个离线 Make targets 和帮助**

`Makefile` 顶部定义 `EARTHLY ?= earthly`、`EARTHLY_FLAGS ?=` 和 `SUITE ?= integration`。每个 target 上方写英文或中文注释；命令只能采用：

```make
# Regenerate and verify protobuf generated code.
proto:
	$(EARTHLY) $(EARTHLY_FLAGS) +proto

# Run Ruff lint/format checks, mypy, and protobuf generated-code verification.
lint:
	$(EARTHLY) $(EARTHLY_FLAGS) +lint

# Run all deterministic offline tests, evaluation, and coverage gates.
test:
	$(EARTHLY) $(EARTHLY_FLAGS) +test

# Run the complete Secret-free pre-commit and pull-request gate.
ci:
	$(EARTHLY) $(EARTHLY_FLAGS) +ci
```

`help` 用普通 `echo` 展示五个当前 target；Task 2 再扩展 Docker 三项。不得通过 `awk`、Python 或 Earthly 生成帮助，以保证 Earthly 尚未安装时仍能查看说明。

- [x] **Step 5: 固定行尾并登记测试职责**

在 `.gitattributes` 添加：

```gitattributes
Earthfile text eol=lf
Makefile text eol=lf
```

在 `tests/TEST.md` 的 Contract 目录树加入 `test_build_entrypoints.py`，并在 Contract 测试函数表登记两个测试及上述职责。

- [x] **Step 6: 运行离线入口契约与既有容器契约**

Run: `uv run pytest tests/contract/test_build_entrypoints.py tests/contract/test_container_artifacts.py -q`

Expected: PASS。

若执行机已安装 Earthly，再运行：`make lint`。若未安装，记录为未运行，不能声称 Earthly 行为已通过。

实际结果：`8 passed`；Ruff check/format 均通过。执行机尚未安装 Earthly，因此 `make lint` 留待 Task 6 的真实环境总验收。

- [x] **Step 7: 更新计划复选框并提交 Task 1**

提交前运行 `git status --short`，确认 `.env.example` 未暂存。

```bash
git add Earthfile Makefile .gitattributes tests/contract/test_build_entrypoints.py tests/TEST.md docs/superpowers/plans/2026-08-25-build-command-orchestration.md
git commit -m "feat(build): 新增统一离线构建入口"
```

---

### Task 2: Docker Compose 启停与参数化真实测试入口

**Files:**
- Modify: `Earthfile`
- Modify: `Makefile`
- Modify: `tests/contract/test_build_entrypoints.py`
- Modify: `tests/TEST.md`
- Modify: `docs/superpowers/plans/2026-08-25-build-command-orchestration.md`

**Interfaces:**
- Consumes: Task 1 的 `EARTHLY`、`EARTHLY_FLAGS`、`SUITE` 变量，现有 `docker-compose.yml`、resilience override 和 `rag-test` 镜像。
- Produces: Earthly function `+docker-start`，targets `+docker-up`、`+docker-test`、`+docker-down`，Make targets `docker-up`、`docker-test`、`docker-down`。

- [x] **Step 1: 扩展失败契约测试**

向 `tests/contract/test_build_entrypoints.py` 添加：

```python
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
    for target in ("docker-up", "docker-test", "docker-down"):
        assert re.search(rf"^# .+\n{re.escape(target)}:", makefile, re.MULTILINE)
        assert re.search(rf"^# .+\n{re.escape(target)}:", earthfile, re.MULTILINE)
    assert "LOCALLY" in earthfile
    assert "docker compose config --quiet" in earthfile
    for suite in ("integration", "resilience", "eval", "all"):
        assert f"{suite})" in earthfile
    assert "Unknown SUITE:" in earthfile
    assert "scripts/check_secret_leaks.py" in earthfile
    assert "docker compose down --remove-orphans" in earthfile
    assert "down -v" not in earthfile
```

- [x] **Step 2: 运行测试并确认 Docker target 缺失**

Run: `uv run pytest tests/contract/test_build_entrypoints.py::test_docker_entrypoints_validate_suites_scan_logs_and_preserve_volumes -q`

Expected: FAIL，指出缺少三个 Docker Make/Earthly targets。

- [x] **Step 3: 在 Earthfile 实现可复用 Docker 启动 function**

使用 `FUNCTION` 让 `+docker-up` 和 `+docker-test` 共用以下唯一启动序列：

```Earthfile
# Validate, build, and start the complete Compose topology; requires Docker and a valid .env.
docker-start:
    FUNCTION
    RUN docker compose config --quiet
    RUN docker compose --profile test build rag-server rag-worker rag-outbox rag-test
    RUN docker compose up -d --wait --wait-timeout 240 rag-server rag-worker rag-outbox

# Start the complete RAG service topology and wait for every declared health condition.
docker-up:
    LOCALLY
    DO +docker-start
```

- [x] **Step 4: 实现参数化 Docker 测试 target**

`+docker-test` 必须先在 shell `case` 中校验 `SUITE`，再调用 `DO +docker-start`，然后以 shell functions 保存三条真实命令。命令必须与当前 `docker-quality.yml` 一致：

```Earthfile
# Run a selected real Docker suite and preserve the service state for diagnosis after failure.
docker-test:
    LOCALLY
    ARG SUITE=integration
    RUN case "$SUITE" in integration|resilience|eval|all) ;; *) echo "Unknown SUITE: $SUITE" >&2; exit 2 ;; esac
    DO +docker-start
    RUN run_integration() { docker compose --profile test run --rm -e RAG_MIGRATIONS_ROOT=/app -e RAG_TEST_MYSQL_DSN=mysql+asyncmy://rag:rag@mysql:3306/rag -e RAG_TEST_ELASTICSEARCH_URL=http://elasticsearch:9200 -e RAG_TEST_NATS_URL=nats://nats:4222 rag-test uv run pytest -m "integration or model_integration or e2e" tests/integration tests/e2e -q; }; \
        run_resilience() { docker compose -f docker-compose.yml -f tests/resilience/docker/docker-compose.resilience.yml config --quiet && docker compose -f docker-compose.yml -f tests/resilience/docker/docker-compose.resilience.yml --profile test build rag-server rag-worker rag-outbox rag-test && docker compose -f docker-compose.yml -f tests/resilience/docker/docker-compose.resilience.yml --profile test run --rm rag-test uv run pytest -m docker_resilience tests/resilience/docker -q; }; \
        run_eval() { docker compose --profile test run --rm rag-test uv run pytest -m eval tests/eval/test_real_retrieval_quality.py -q; }; \
        case "$SUITE" in integration) run_integration ;; resilience) run_resilience ;; eval) run_eval ;; all) run_integration && run_resilience && run_eval ;; esac
```

- [x] **Step 5: 实现安全清理 target**

`+docker-down` 使用一个 `RUN` 完成日志采集、Secret 扫描和清理，确保扫描失败后仍执行 down：

```Earthfile
# Scan Compose logs for the configured API key, then stop services without deleting volumes.
docker-down:
    LOCALLY
    RUN log_file="$(mktemp)"; trap 'rm -f "$log_file"' EXIT; \
        docker compose logs --no-color >"$log_file" 2>&1 || true; \
        scan_status=0; \
        docker compose --profile test run --rm -T --no-deps rag-test uv run python scripts/check_secret_leaks.py <"$log_file" || scan_status=$?; \
        down_status=0; docker compose down --remove-orphans || down_status=$?; \
        if [ "$scan_status" -ne 0 ]; then exit "$scan_status"; fi; exit "$down_status"
```

Earthfile 中 shell 变量使用单个 `$`，最终脚本传给 shell 的变量必须是 `$?`、`$scan_status` 和 `$down_status`。

- [x] **Step 6: 扩展 Makefile 到最终八个公共目标**

新增三个带注释 target：

```make
# Validate, build, start, and wait for the complete Docker Compose topology.
docker-up:
	$(EARTHLY) $(EARTHLY_FLAGS) +docker-up

# Run a real Docker suite; SUITE accepts integration, resilience, eval, or all.
docker-test:
	$(EARTHLY) $(EARTHLY_FLAGS) +docker-test --SUITE=$(SUITE)

# Scan service logs and stop Compose services without deleting persistent volumes.
docker-down:
	$(EARTHLY) $(EARTHLY_FLAGS) +docker-down
```

同步扩展 `.PHONY` 和 `help`，显示 `SUITE=integration|resilience|eval|all`，默认值为 `integration`。

- [x] **Step 7: 更新测试登记并运行契约测试**

在 `tests/TEST.md` 登记新增测试函数。

Run: `uv run pytest tests/contract/test_build_entrypoints.py tests/contract/test_container_artifacts.py -q`

Expected: PASS。

实际结果：构建入口与容器契约合计 `9 passed`；Ruff check/format 和 `make help` 均通过。Earthly/Docker 行为留待 Task 6 真实验收。

- [x] **Step 8: 更新计划复选框并提交 Task 2**

```bash
git add Earthfile Makefile tests/contract/test_build_entrypoints.py tests/TEST.md docs/superpowers/plans/2026-08-25-build-command-orchestration.md
git commit -m "feat(build): 封装 Docker 服务与真实测试入口"
```

---

### Task 3: Git pre-commit 与快速 GitHub Actions 对齐

**Files:**
- Modify: `.githooks/pre-commit`
- Modify: `.github/workflows/quality.yml`
- Modify: `Makefile`
- Create: `.earthly.env`
- Modify: `tests/contract/test_build_entrypoints.py`
- Modify: `tests/contract/test_container_artifacts.py`
- Modify: `tests/TEST.md`
- Modify: `docs/superpowers/plans/2026-08-25-build-command-orchestration.md`

**Interfaces:**
- Consumes: Task 1 的 `make ci`。
- Produces: Hook 和无 Secret GitHub workflow 的唯一快速门禁调用 `make ci`，CI 固定 Earthly `v0.8.16`。

- [x] **Step 1: 写入 Hook 与 quick workflow 的失败契约**

向 `test_build_entrypoints.py` 添加：

```python
def test_hook_and_quick_workflow_delegate_only_to_make_ci() -> None:
    hook_lines = [
        line.strip()
        for line in _text(".githooks/pre-commit").splitlines()
        if line.strip()
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
```

- [x] **Step 2: 运行测试并确认旧 Hook/Workflow 仍复制底层命令**

Run: `uv run pytest tests/contract/test_build_entrypoints.py::test_hook_and_quick_workflow_delegate_only_to_make_ci -q`

Expected: FAIL。

- [x] **Step 3: 把 Hook 收敛为 make ci**

`.githooks/pre-commit` 的完整内容改为：

```sh
#!/bin/sh
set -eu

make ci
```

- [x] **Step 4: 把 quick workflow 收敛为 Earthly + Make**

保留 `push`、`pull_request`、只读权限和 15 分钟 timeout，把 steps 改为：

```yaml
env:
  EARTHLY_FLAGS: --ci

steps:
  - name: Check out repository
    uses: actions/checkout@v4

  - name: Install Earthly
    uses: earthly/actions-setup@v1
    with:
      version: "v0.8.16"

  - name: Run the Secret-free quality gate
    run: make ci
```

- [x] **Step 5: 更新既有工作流分离断言和测试登记**

在 `test_container_artifacts.py::test_quality_workflows_keep_offline_and_secret_backed_suites_separate` 中把 quick workflow 的 `eval and not e2e` 断言替换为 `run: make ci`、`secrets. not in quick` 和 `pull_request:`；Docker workflow 的断言留到 Task 4 修改。

在 `tests/TEST.md` 登记新增函数，并更新既有容器契约函数的职责说明。

- [x] **Step 6: 运行构建与容器契约测试**

Run: `uv run pytest tests/contract/test_build_entrypoints.py tests/contract/test_container_artifacts.py -q`

Expected: PASS。

- [x] **Step 7: 更新计划复选框并提交 Task 3**

```bash
git add .githooks/pre-commit .github/workflows/quality.yml tests/contract/test_build_entrypoints.py tests/contract/test_container_artifacts.py tests/TEST.md docs/superpowers/plans/2026-08-25-build-command-orchestration.md
git commit -m "ci: 统一快速质量门禁入口"
```

---

### Task 4: 真实 Docker GitHub Actions 对齐

**Files:**
- Modify: `.github/workflows/docker-quality.yml`
- Modify: `tests/contract/test_build_entrypoints.py`
- Modify: `tests/contract/test_container_artifacts.py`
- Modify: `tests/TEST.md`
- Modify: `docs/superpowers/plans/2026-08-25-build-command-orchestration.md`

**Interfaces:**
- Consumes: Task 2 的 `make docker-test SUITE=...` 和 `make docker-down`。
- Produces: main/manual/nightly jobs 复用同一 Earthfile Docker 命令，同时保留 Secret 预检与 always cleanup。

- [ ] **Step 1: 写入 Docker workflow 的失败契约**

向 `test_build_entrypoints.py` 添加：

```python
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
```

- [ ] **Step 2: 运行测试并确认旧 workflow 仍复制 Compose/pytest 命令**

Run: `uv run pytest tests/contract/test_build_entrypoints.py::test_docker_workflow_delegates_real_suites_and_always_cleans_up -q`

Expected: FAIL。

- [ ] **Step 3: 重写 integration/E2E job steps**

保留四个 Secret 的具名非空检查，并在 job `env` 中增加 `EARTHLY_FLAGS: --ci`。其余 steps 改为：

```yaml
- name: Install Earthly
  uses: earthly/actions-setup@v1
  with:
    version: "v0.8.16"

- name: Run real adapters, model, and four-format gRPC tests
  run: make docker-test SUITE=integration

- name: Scan logs and stop services
  if: always()
  run: make docker-down
```

- [ ] **Step 4: 重写 nightly job steps**

同样保留 Secret 检查、在 job `env` 中增加 `EARTHLY_FLAGS: --ci` 并安装固定 Earthly，然后执行：

```yaml
- name: Run Docker crash-recovery suite
  run: make docker-test SUITE=resilience

- name: Run real thirty-question retrieval evaluation
  run: make docker-test SUITE=eval

- name: Scan logs and stop services
  if: always()
  run: make docker-down
```

- [ ] **Step 5: 更新既有容器契约和测试登记**

`test_container_artifacts.py::test_quality_workflows_keep_offline_and_secret_backed_suites_separate` 改为断言：quick 使用 `make ci`；Docker workflow 包含三个 `make docker-test` suite、两个 `make docker-down`、Secret 引用、schedule/workflow_dispatch，且不含 `pull_request_target:` 或 `down -v`。

在 `tests/TEST.md` 登记新增函数并更新既有职责。

- [ ] **Step 6: 运行全部 Contract 与离线快速测试**

Run: `uv run pytest tests/contract -q`

Expected: PASS。

Run: `uv run pytest tests/unit tests/contract tests/functional -q`

Expected: PASS。

- [ ] **Step 7: 更新计划复选框并提交 Task 4**

```bash
git add .github/workflows/docker-quality.yml tests/contract/test_build_entrypoints.py tests/contract/test_container_artifacts.py tests/TEST.md docs/superpowers/plans/2026-08-25-build-command-orchestration.md
git commit -m "ci(docker): 复用 Earthly 真实测试编排"
```

---

### Task 5: 规格、协作规则与使用文档同步

**Files:**
- Modify: `docs/SPEC.md`
- Modify: `docs/testing-guide.md`
- Modify: `docs/README.md`
- Modify: `AGENTS.md`
- Modify: `docs/superpowers/plans/2026-08-25-build-command-orchestration.md`

**Interfaces:**
- Consumes: Tasks 1～4 已验证的八个 Make target 和四个 Docker suite。
- Produces: 权威规格、协作指令和用户文档统一引用 Makefile；底层单文件 pytest 命令仅保留为排错入口。

- [ ] **Step 1: 在 SPEC 固化工具与入口约束**

在 `3.1 运行时与开发工具` 增加：

```markdown
| 构建入口 | Earthly 0.8.16 + GNU Make | Earthfile 保存完整命令与执行环境；Makefile 只暴露稳定高层入口。Git Hook、CI 和 README 不复制底层命令。 |
```

在 `4.6 覆盖率与质量门禁` 明确 `make lint`、`make test`、`make ci` 的边界；在 `4.7 本地提交质量门禁` 把 Hook 命令列表收敛为 `make ci`，并说明它不运行真实 Docker suite。在 `5.2 建议目录树` 加入 Earthfile、Makefile 和新 contract test。

- [ ] **Step 2: 更新 AGENTS 的强制执行规则**

在“可靠性与测试”加入：

```markdown
- 日常完整离线门禁优先运行 `make ci`；只定位单个失败时可直接运行底层 `uv run pytest ...`。真实验收通过 `make docker-test SUITE=integration|resilience|eval|all` 选择，禁止在 Hook、CI 或文档中复制 Earthfile 已维护的完整命令。
- 修改 `Earthfile`、`Makefile`、Git Hook 或 GitHub Actions 时，必须运行 `tests/contract/test_build_entrypoints.py`，并验证公开 Make target、Secret 隔离和 Docker 持久卷保护不变量。
```

在提交规则的“提交前运行与改动相称的检查”后补充：能覆盖改动时优先报告 `make lint`、`make test`、`make ci` 或实际 Docker suite；若 Earthly 未安装必须明确说明。

- [ ] **Step 3: 用高层命令重构 testing guide 的常用路径**

更新环境要求为 Docker、GNU Make、Earthly 0.8.16；Windows 推荐 WSL2。日常验证改为：

```bash
make lint
make test
# 或一次运行全部快速门禁
make ci
```

protobuf 改为 `make proto`。真实基础设施章节改为：

```bash
make docker-up
make docker-test SUITE=integration
make docker-test SUITE=resilience
make docker-test SUITE=eval
make docker-down
```

保留“按功能验证”表内的单测试文件 `uv run pytest`，用于失败定位而非正式公共入口。明确 `SUITE=all` 的执行顺序、真实费用、resilience 的进程控制以及测试失败后服务不会自动关闭。

- [ ] **Step 4: 暂时精简 docs README 的命令区**

在后续完整 README 重写前，只把现有 protobuf、质量检查和 Docker 长命令替换为：

```bash
make proto
make ci
make docker-up
make docker-test SUITE=integration
make docker-down
```

README 链接到 `testing-guide.md` 获取 suite、排错与底层命令，不在本任务迁移根 README，也不改 `pyproject.toml` 或 `Dockerfile` 的 README 路径。

- [ ] **Step 5: 运行文档相关契约与统一离线入口**

Run: `uv run pytest tests/contract/test_build_entrypoints.py tests/contract/test_container_artifacts.py -q`

Expected: PASS。

Run: `make ci`

Expected: PASS。若 Earthly 仍不可用，停止任务并请求用户提供安装权限或可执行路径；不得用旧 Hook 的宿主机命令冒充 `make ci` 验收。

- [ ] **Step 6: 更新计划复选框并提交 Task 5**

```bash
git add docs/SPEC.md docs/testing-guide.md docs/README.md AGENTS.md docs/superpowers/plans/2026-08-25-build-command-orchestration.md
git commit -m "docs(build): 统一构建与测试使用入口"
```

---

### Task 6: 真实环境总验收与结果记录

**Files:**
- Modify: `docs/testing-guide.md`
- Modify: `docs/superpowers/plans/2026-08-25-build-command-orchestration.md`

**Interfaces:**
- Consumes: 最终八个 Make targets、有效 `.env`、Docker Desktop 和 Earthly 0.8.16。
- Produces: 可复核的离线、integration/E2E、Docker resilience、real eval 和安全清理结果；不修改业务数据契约。

- [ ] **Step 1: 执行工具与工作区前置检查**

Run:

```powershell
make --version
earthly --version
docker version
docker compose version
git status --short
```

Expected: GNU Make 可用；Earthly 为 `v0.8.16`；Docker daemon/Compose 可用；`.env.example` 仍保持未暂存且 `.env` 不出现在 Git 状态中。

- [ ] **Step 2: 运行 protobuf、Lint 和完整离线门禁**

Run:

```bash
make proto
git diff --exit-code -- src/rag_mvp/rpc/generated
make lint
make test
make ci
```

Expected: 全部 PASS；protobuf 不产生意外 diff；覆盖率不低于 85%。分别记录 pytest 数量、deselected 数量和最终覆盖率。

- [ ] **Step 3: 启动并验证完整 Docker 拓扑**

Run:

```bash
make docker-up
docker compose ps --format json
```

Expected: `rag-migrate` 成功退出，MySQL、Elasticsearch、NATS、Server、Worker 和 Outbox 健康或处于预期运行状态；不得输出渲染后的 Secret。

- [ ] **Step 4: 运行所有真实测试 suite**

Run:

```bash
make docker-test SUITE=integration
make docker-test SUITE=resilience
make docker-test SUITE=eval
```

Expected: 真实 adapter/model/四格式 E2E、Docker KILL/recovery 和 30 问评测全部 PASS。若某组失败，保留现场、记录失败命令和结果，先诊断再继续；不得直接跳到清理并宣称通过。

- [ ] **Step 5: 扫描日志并安全停止服务**

Run: `make docker-down`

Expected: Secret scanner 返回成功，Compose 服务停止，命令未执行 `down -v`。随后运行 `docker volume ls --format '{{.Name}}'`，确认 `rag-mvp` 的 MySQL、ES、NATS 和对象卷仍存在。

- [ ] **Step 6: 记录实际验收数据**

在 `docs/testing-guide.md` 的发布证据后追加“统一构建入口验收（2026-08-25）”，只记录实际运行的：Earthly 版本、离线通过数、覆盖率、integration/E2E 通过数、resilience 通过数、eval 指标和未运行项。禁止复制 API Key、模型 URL、向量或完整 Compose 配置。

- [ ] **Step 7: 运行最终静态检查并提交验收记录**

Run:

```bash
git diff --check
uv run pytest tests/contract/test_build_entrypoints.py tests/contract/test_container_artifacts.py -q
git status --short
```

Expected: PASS；只暂存验收记录和本计划，不包含 `.env.example`。

```bash
git add docs/testing-guide.md docs/superpowers/plans/2026-08-25-build-command-orchestration.md
git commit -m "docs(test): 记录统一构建入口验收结果"
```

---

### Task 7: 迁移并重写 GitHub 根 README

**Files:**
- Create: `README.md`
- Delete: `docs/README.md`
- Modify: `pyproject.toml`
- Modify: `Dockerfile`
- Modify: `tests/contract/test_container_artifacts.py`
- Modify: `tests/TEST.md`
- Modify: `docs/SPEC.md`
- Modify: `docs/superpowers/plans/2026-08-25-build-command-orchestration.md`

**Interfaces:**
- Consumes: Task 6 已验收的八个 Make targets、当前 gRPC `rag-dev` 客户端、`LICENSE` 和权威设计文档。
- Produces: GitHub 与 Python package 共用的根 `README.md`；Docker runtime/test 构建上下文也只复制根 README。

- [ ] **Step 1: 写入根 README 路径的失败契约**

在 `tests/contract/test_container_artifacts.py` 增加 `test_package_and_container_use_canonical_root_readme`：断言根 `README.md` 存在、`docs/README.md` 不存在、`pyproject.toml` 声明 `readme = "README.md"`，并且 Dockerfile 的 build-base/test 阶段只复制根 README。先运行该测试，确认它因根 README 尚不存在而失败。

Run: `uv run pytest tests/contract/test_container_artifacts.py::test_package_and_container_use_canonical_root_readme -q`

Expected: FAIL，原因是 `README.md` 不存在。

- [ ] **Step 2: 迁移 README 的权威路径**

使用 `apply_patch` 创建根 `README.md`、删除 `docs/README.md`，将 `pyproject.toml` 改为：

```toml
readme = "README.md"
```

将 Dockerfile 中两处 README 复制改为：

```dockerfile
COPY README.md ./README.md
```

同步更新 `docs/SPEC.md` 的建议目录树与构建说明，不保留两个内容重复的 README。

- [ ] **Step 3: 按读者路径重写 README**

根 README 按以下顺序组织，每节只保留读者完成当前动作所需的信息：

1. 项目定位、当前成熟度和 Python/未来 Go 职责边界；
2. 已实现能力与明确非目标；
3. `Client → gRPC Server → MySQL/Object Storage → Outbox/NATS → Worker → ES` 架构和摄取/检索数据流；
4. Docker 最短启动路径：准备 `.env`、`make docker-up`、`make docker-down`；
5. `rag-dev` 或 generated gRPC client 的最小调用入口，不新增 HTTP 示例；
6. 本地开发：`make proto`、`make lint`、`make test`、`make ci`；
7. 测试分层概览，并链接 `docs/testing-guide.md`；
8. 精简仓库目录树；
9. 配置、Secret 和持久卷安全说明；
10. 当前路线图、权威文档链接和 Apache-2.0 许可证。

README 不复制完整 pytest、Docker Compose、CI 或故障恢复长命令，不展示模型 URL/API Key，不把 Python 描述成 Answer/Agent/SSE 服务。

- [ ] **Step 4: 更新测试职责并验证 package/container 契约**

在 `tests/TEST.md` 登记新增测试函数。运行：

```bash
uv run pytest tests/contract/test_container_artifacts.py tests/contract/test_build_entrypoints.py -q
uv build
docker compose --profile test build rag-server rag-worker rag-outbox rag-test
```

Expected: Contract PASS；sdist/wheel 成功构建并包含根 README 元数据；runtime/test 镜像构建成功。

- [ ] **Step 5: 运行 README 最终内容检查**

Run:

```bash
git diff --check
rg -n "docs/README.md|uv run pytest.*tests/(unit|contract|functional)|docker compose .*pytest" README.md pyproject.toml Dockerfile docs/SPEC.md
```

Expected: `git diff --check` PASS；`rg` 不在根 README、package metadata、Dockerfile 或 SPEC 中发现旧 README 路径或重新引入的长测试命令。

- [ ] **Step 6: 更新计划复选框并提交 Task 7**

提交前检查根 README 链接均使用仓库相对路径，`.env.example` 仍未暂存。

```bash
git add README.md docs/README.md pyproject.toml Dockerfile tests/contract/test_container_artifacts.py tests/TEST.md docs/SPEC.md docs/superpowers/plans/2026-08-25-build-command-orchestration.md
git commit -m "docs(readme): 重写项目首页与快速入门"
```

---

## Completion Criteria

- 八个 Make target 与所有 Earthly target/function 都有说明注释。
- README、Git Hook、GitHub Actions 和测试指南的常用路径只引用 Makefile。
- protobuf、质量检查和离线测试在固定 Earthly Python 环境成功运行。
- quick CI 不接触 Secret；Docker CI 保留 Secret 预检、真实 suite 和 always cleanup。
- `SUITE` 非法值 fail fast，`docker-down` 扫描日志且绝不删除持久卷。
- 新增构建契约测试和 `tests/TEST.md` 完全对齐。
- `make ci`、三个真实 Docker suite 和 `make docker-down` 均有实际验收记录；任何未运行项被明确标注。
- GitHub、Python package 和 Docker 镜像统一使用根 `README.md`，项目首页按读者路径组织且不复制底层长命令。
- 每个 Task 独立提交，`.env`、API Key、模型 URL 和外部 `.env.example` 修改均未进入提交。
