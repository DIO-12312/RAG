# RAG MVP

可靠、可恢复的 Python RAG 计算服务。Python 只通过版本化 gRPC 提供文档摄取、检索与 evidence 能力；未来的公网 API、鉴权、会话、Chat Model、Agent Harness 和 SSE 由 Go 控制面负责。

当前已完成 Milestone B 的 Mock Functional 闭环：`CreateDataset`、TXT `SubmitDocument`、`GetJob` 与 Dense `Retrieve` 可通过真实 gRPC/application/Outbox/Worker/pipeline 调用链运行。MySQL、Elasticsearch、NATS JetStream 的真实 adapter 与 Compose 验收仍未完成，因此当前状态不是内部 Alpha 发布出口。

## 环境要求

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Docker Compose（仅在运行真实 MySQL、Elasticsearch 与 NATS JetStream 集成测试时需要）

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
```

无需 Docker 时，`tests/functional/` 使用真实 gRPC 与本地文件对象存储，只将 MetadataRepository、TaskQueue、SearchEngine 和 ModelGateway 替换为 `tests/fakes/` 中的确定性实现。Fake 不会被生产 `bootstrap/container.py` 导入。

为当前 clone 启用仓库内的提交门禁：

```powershell
git config core.hooksPath .githooks
```

## 本地依赖

Mock Functional 开发和快速门禁不需要启动以下服务。真实基础设施 integration/E2E 验收才使用：

```powershell
docker compose up -d mysql elasticsearch nats
docker compose ps
docker compose down
```

不要使用 `docker compose down -v` 做恢复测试，否则会删除需要验证的持久数据。

## 进程入口

```powershell
uv run rag-server
uv run rag-worker
uv run rag-outbox
uv run rag-dev --help
```

本地手工调试必须使用 generated gRPC client、`grpcurl`、`grpcui` 或 `rag-dev`，不得新增 HTTP/FastAPI adapter。

## 设计文档

- `SPEC.md`：权威架构、状态机、RPC 与测试不变量。
- `PLAN.md`：Milestone 和交付顺序。
- `plans/`：当前 Milestone 的逐文件实施计划。
- `AGENTS.md`：仓库协作与可靠性约束。
