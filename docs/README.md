# RAG MVP

可靠、可恢复的 Python RAG 计算服务。Python 只通过版本化 gRPC 提供文档摄取、检索与 evidence 能力；未来的公网 API、鉴权、会话、Chat Model、Agent Harness 和 SSE 由 Go 控制面负责。

当前已完成 MySQL、Elasticsearch、NATS JetStream、OpenAI-compatible Embedding 的真实 adapter，以及分离的 gRPC Server、Worker、Outbox、Migration 与测试镜像拓扑。Mock Functional/Resilience 和真实基础设施 integration 已有自动化证据；四格式 Docker gRPC E2E 与进程 `KILL` 恢复仍属于后续验收，因此当前状态还不是最终发布基线。

## 环境要求

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Docker Compose（运行真实基础设施 integration、完整服务或 E2E 时需要）

## 安装

```powershell
Copy-Item .env.example .env
uv sync --frozen --group dev
```

`.env` 只用于本地开发，不得提交密钥或真实用户数据。

## 生成 protobuf

```powershell
uv run python scripts/generate_proto.py
uv run python scripts/check_generated.py
```

`src/rag_mvp/rpc/generated/` 由脚本生成，禁止手工编辑。

## 质量检查

```powershell
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run mypy src scripts
uv run python scripts/check_generated.py
uv run pytest tests/unit tests/contract
uv run pytest tests/functional
uv run pytest -m resilience tests/resilience
uv run pytest -m eval tests/eval
uv run pytest --cov=rag_mvp.domain --cov=rag_mvp.application --cov=rag_mvp.ingestion --cov=rag_mvp.retrieval --cov-fail-under=85 tests/unit tests/contract tests/functional tests/resilience tests/eval
```

无需 Docker 时，`tests/functional/` 使用真实 gRPC 与本地文件对象存储，只将 MetadataRepository、TaskQueue、SearchEngine 和 ModelGateway 替换为 `tests/fakes/` 中的确定性实现。它覆盖 TXT、Markdown、Python 和文本 PDF 的 upload → ingest → hybrid retrieve，以及重试、逻辑删除和异步清理。Fake 不会被生产 `bootstrap/container.py` 导入。

固定 30 问评测集当前门禁为 `Recall@6 ≥ 0.85`、`MRR@6 ≥ 0.70`、locator accuracy `= 1.0`；核心 `domain/application/ingestion/retrieval` 聚合覆盖率不得低于 85%。

`tests/fixtures/reliability_matrix.json` 将 SPEC T1～T25 映射到 Mock 测试证据，并逐项标记是否仍需真实基础设施复验。Fake 测试验证状态机与故障编排，但不能替代 MySQL `SELECT FOR UPDATE`、Elasticsearch mapping/KNN/BM25、JetStream durable consumer/ACK/NAK 或进程级强杀恢复。

为当前 clone 启用仓库内的提交门禁：

```powershell
git config core.hooksPath .githooks
```

## 完整 Docker 服务

先在未提交的 `.env` 中配置真实 Embedding provider。只使用 `--quiet` 校验 Compose，避免渲染后的配置把 Secret 输出到终端：

```powershell
docker compose config --quiet
docker compose build rag-server rag-worker rag-outbox
docker compose up -d rag-server rag-worker rag-outbox
uv run python scripts/docker_healthcheck.py
```

`rag-migrate` 会先执行 `upgrade head`，只有成功后应用进程才启动。需要单独验证迁移幂等性时可运行：

```powershell
docker compose run --rm rag-migrate
```

真实 adapter integration 使用宿主机映射端口：

```powershell
uv run pytest -m "integration and not model_integration" tests/integration
uv run pytest -m model_integration tests/integration/test_real_embedding_model.py
```

测试镜像不内置测试源码，而是通过只读 mount 使用当前工作区：

```powershell
docker compose --profile test build rag-test
docker compose --profile test run --rm rag-test uv run pytest tests/contract/test_container_artifacts.py
```

关闭服务但保留持久数据：

```powershell
docker compose down
```

不要使用 `docker compose down -v` 做恢复测试，否则会删除 MySQL、ES、NATS 与对象卷中需要验证的状态。

## 进程入口

```powershell
uv run rag-server
uv run rag-worker
uv run rag-outbox
uv run rag-dev --help
```

`rag-dev` 已覆盖 Dataset 创建、流式上传、Job 查询/重试/取消、检索和删除。本地手工调试必须使用 generated gRPC client、`grpcurl`、`grpcui` 或 `rag-dev`，不得新增 HTTP/FastAPI adapter。

## 设计文档

- [`SPEC.md`](SPEC.md)：权威架构、状态机、RPC 与测试不变量。
- [`PLAN.md`](PLAN.md)：Milestone 和交付顺序。
- [`plans/`](plans/)：当前 Milestone 的逐文件实施计划。
- [`testing-guide.md`](testing-guide.md)：本机 Mock 功能、可靠性和评测验证指南。
- [`../AGENTS.md`](../AGENTS.md)：仓库协作与可靠性约束。
