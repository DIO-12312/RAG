# Milestone A：工程基线实施计划

> 状态：本地实现门禁通过；Docker/CI 发布出口延期，已按 `docs/PLAN.md` 2.1 进入 Mock Functional 开发通道
> 对应路线图：`docs/PLAN.md` A1～A5
> 对应规格：`docs/SPEC.md` 3.1、4.1～4.7、5.1～5.2、5.4、5.7、Phase 0（P0-1～P0-3）
> 范围：只建立可生成、检查、测试和启动的 Python RAG 工程基线，不实现 Document、Job、Task、Outbox、摄取或检索业务行为。

## 1. 目标与阶段边界

Milestone A 完成后，仓库应具备以下能力：

1. 使用 Python 3.12+ 与 `uv` 创建可复现环境；Ruff、mypy、pytest 和覆盖率配置可运行。
2. `proto/rag/v1/rag_service.proto` 一次定义 `RagService` 的 7 个 RPC 及其完整字段级契约，并可重复生成 Python 代码。
3. 建立 `domain/application/ports/adapters/rpc/ingestion/retrieval/outbox/bootstrap/dev` 的最终目录边界；测试阻止越层依赖。
4. gRPC Server、Worker、Outbox 三个进程入口可以加载配置、装配空实现、响应退出信号并干净结束；导入模块时不连接外部服务。
5. Docker Compose 能启动 MySQL、Elasticsearch 和 NATS JetStream，并为三者提供 healthcheck；应用进程在基础设施健康后启动。
6. 本地 pre-commit hook 与 GitHub Actions 执行相同的快速质量检查。

本 Milestone 明确不做：

- 不创建 MySQL 业务 schema 或 migration。
- 不实现对象上传、Outbox、JetStream 发布/消费、Worker pipeline、Elasticsearch 索引或模型调用。
- 不实现 HTTP/FastAPI adapter、Chat、Prompt、Citation 编号、Agent Loop、MCP、会话或 SSE。
- 不开放任何业务 RPC；空服务对格式正确但尚未开放的调用统一返回 `BusinessError(code="FEATURE_NOT_AVAILABLE")`。RPC 传输层异常只用于畸形请求、deadline、不可用和未处理异常。
- 不实施 `docs/PLAN.md` Milestone B～E。

## 2. 目标文件树

Milestone A 仅创建或修改下列文件；生成文件由命令产生，禁止手工编辑：

```text
RAG/
├─ .dockerignore
├─ .env.example
├─ .gitignore
├─ .githooks/
│  └─ pre-commit
├─ .github/
│  └─ workflows/
│     └─ quality.yml
├─ LICENSE
├─ docs/README.md
├─ docker-compose.yml
├─ pyproject.toml
├─ uv.lock
├─ proto/
│  └─ rag/v1/rag_service.proto
├─ scripts/
│  ├─ generate_proto.py
│  └─ check_generated.py
├─ src/rag_mvp/
│  ├─ __init__.py
│  ├─ main.py
│  ├─ config.py
│  ├─ application/
│  │  ├─ __init__.py
│  │  └─ dto.py
│  ├─ domain/
│  │  └─ __init__.py
│  ├─ ports/
│  │  ├─ __init__.py
│  │  ├─ metadata.py
│  │  ├─ storage.py
│  │  ├─ message_queue.py
│  │  ├─ search_engine.py
│  │  ├─ model.py
│  │  ├─ parser.py
│  │  └─ chunker.py
│  ├─ adapters/
│  │  ├─ __init__.py
│  │  ├─ metadata/__init__.py
│  │  ├─ storage/__init__.py
│  │  ├─ message_queue/__init__.py
│  │  ├─ search_engine/__init__.py
│  │  ├─ model/__init__.py
│  │  ├─ parsers/__init__.py
│  │  └─ chunkers/__init__.py
│  ├─ rpc/
│  │  ├─ __init__.py
│  │  ├─ server.py
│  │  ├─ rag_service.py
│  │  ├─ interceptors.py
│  │  └─ generated/
│  │     ├─ __init__.py
│  │     ├─ rag_service_pb2.py
│  │     ├─ rag_service_pb2.pyi
│  │     └─ rag_service_pb2_grpc.py
│  ├─ ingestion/
│  │  ├─ __init__.py
│  │  └─ worker.py
│  ├─ retrieval/
│  │  └─ __init__.py
│  ├─ outbox/
│  │  ├─ __init__.py
│  │  ├─ main.py
│  │  ├─ finalizer.py
│  │  └─ relay.py
│  ├─ bootstrap/
│  │  ├─ __init__.py
│  │  └─ container.py
│  └─ dev/
│     ├─ __init__.py
│     └─ cli.py
└─ tests/
   ├─ conftest.py
   ├─ unit/
   │  ├─ test_config.py
   │  ├─ test_import_boundaries.py
   │  └─ test_process_lifecycle.py
   ├─ contract/
   │  ├─ test_proto_contract.py
   │  └─ test_generated_code.py
   ├─ integration/.gitkeep
   ├─ e2e/.gitkeep
   ├─ resilience/.gitkeep
   ├─ evals/.gitkeep
   └─ fixtures/.gitkeep
```

说明：

- `application/dto.py` 和各 `ports/*.py` 在本阶段只定义稳定的最小类型骨架或 `Protocol` 名称，不引入尚未验证的业务字段和 SDK。
- `adapters/` 本阶段只建立包边界，不创建 MySQL、NATS、Elasticsearch 或模型 concrete adapter。
- `finalizer.py`、`relay.py` 和 `worker.py` 只提供可注入、可停止的空运行循环；不得读写任务或执行 ACK/NAK。
- `.gitkeep` 只用于保留后续测试层目录，不在 Milestone A 编造业务测试。

## 3. A1：Python 工程与质量配置

### 3.1 产出

创建：

- `pyproject.toml`：包元数据、Python `>=3.12`、运行依赖、开发依赖、Ruff、mypy、pytest、coverage 和 pytest markers。
- `uv.lock`：由 `uv lock` 生成。
- `src/rag_mvp/__init__.py`：包版本常量，不触发配置加载或连接。
- `.gitignore`：忽略 `.env`、虚拟环境、缓存、覆盖率、构建产物、日志和 `data/`；不得忽略生成的 protobuf Python 文件。
- `.env.example`：仅含无密钥的本地默认值与占位符。
- `docs/README.md`：环境安装、proto 生成、质量检查、Compose 和三个进程入口命令。
- `LICENSE`：Apache-2.0 许可证文本；不得拷贝 RAGFlow 源码。
- `tests/conftest.py` 及测试层目录。

`pyproject.toml` 的直接运行依赖固定为本阶段实际使用的最小集合：

- `grpcio`
- `protobuf`
- `pydantic-settings`
- `structlog`

开发依赖固定为：

- `grpcio-tools`
- `mypy`
- `pytest`
- `pytest-asyncio`
- `pytest-cov`
- `ruff`
- `types-protobuf`

MySQL、Elasticsearch、NATS、模型与文档解析 SDK 延后到对应 adapter 首次实现时加入，避免 Milestone A 引入未使用依赖。

### 3.2 先失败测试

1. 包导入 smoke test：`import rag_mvp` 成功，且导入不读取 `.env`、不打开 socket、不创建线程。
2. 配置测试：缺失必填生产配置时给出字段级校验错误；测试/开发配置可显式构造，不依赖真实环境变量。
3. marker 注册测试：`integration/e2e/resilience/eval` 均在 pytest 配置中注册，避免后续未知 marker 被静默接受。

预期初始失败原因：工程文件和包尚不存在。

### 3.3 最小实现

1. 配置 `src` layout 和 `uv` dependency groups。
2. 创建无副作用包入口与集中 Settings。
3. 配置 Ruff 的 lint/format、mypy strict 基线、pytest `strict_markers=true` 和 coverage source。
4. 生成 lockfile 后运行安装与 smoke test。

### 3.4 验证命令

```powershell
uv sync --frozen --group dev
uv run python -c "import rag_mvp; print(rag_mvp.__version__)"
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run mypy src scripts
uv run pytest tests/unit tests/contract
```

## 4. A2：完整 protobuf 契约

### 4.1 服务接口

`proto/rag/v1/rag_service.proto` 使用 `package rag.v1`，定义：

```proto
service RagService {
  rpc CreateDataset(CreateDatasetRequest) returns (CreateDatasetResponse);
  rpc SubmitDocument(stream UploadDocumentRequest) returns (SubmitDocumentResponse);
  rpc GetJob(GetJobRequest) returns (GetJobResponse);
  rpc RetryJob(RetryJobRequest) returns (RetryJobResponse);
  rpc CancelJob(CancelJobRequest) returns (CancelJobResponse);
  rpc Retrieve(RetrieveRequest) returns (RetrieveResponse);
  rpc DeleteDocument(DeleteDocumentRequest) returns (DeleteDocumentResponse);
}
```

### 4.2 公共消息与枚举

必须一次定义并由 contract test 固定：

- `RequestContext { request_id, idempotency_key }`
- `BusinessError { code, message, retryable, request_id }`
- `JobStatus` 与 `TaskStatus`：`UNSPECIFIED/PENDING/RUNNING/SUCCEEDED/FAILED/CANCELLED`
- `JobType`：`UNSPECIFIED/INGEST_DOCUMENT/DELETE_DOCUMENT/CLEANUP_INDEX_VERSION`
- `Locator`：可选 `page_number/start_line/end_line/symbol/language` 与扩展 metadata
- `ScoreBreakdown`：可选 `dense_score/sparse_score/fusion_score/rerank_score`
- 受限 metadata filter：只允许结构化 `key/value` 精确过滤；本阶段不接受任意 ES DSL

字段使用明确的 optional/oneof 语义，所有枚举保留 `0 = *_UNSPECIFIED`，现有字段号一经生成不得重用。

### 4.3 请求与响应契约

- `CreateDatasetRequest`：`context/name/embedding_model/embedding_dimension/retrieval_config`。
- `CreateDatasetResult`：`dataset_id/name/embedding_model/embedding_dimension`。
- `UploadHeader`：`context/dataset_id/source_name/expected_sha256/target_document_id`。
- `UploadDocumentRequest`：`oneof payload { UploadHeader header; bytes data; }`；header 只允许首帧，流规则由后续 application/RPC 测试实现。
- `SubmitDocumentResult`：`document_id/job_id/reused`。
- `GetJobRequest`：`request_id/job_id`。
- `JobResult`：`job_id/document_id/type/status/progress/error/retryable/retry_count/cancel_requested/task_status`。
- `RetryJobRequest`：`context/job_id`；结果复用 `JobResult`。
- `CancelJobRequest`：`context/job_id`；结果必须同时返回实际 Job 与 Task 状态。
- `RetrieveRequest`：`request_id/dataset_id/query/filters/top_k/enable_rerank/max_context_tokens`。
- `Evidence`：`chunk_id/document_id/content_with_weight/source_name/locator/metadata/scores/index_version`。
- `RetrieveResult`：evidence 列表、估算 token 数和被预算排除的 chunk IDs；不含答案、Prompt 或 `[n]` Citation 编号。
- `DeleteDocumentRequest`：`context/document_id`；结果返回 `document_id/job_id/document_status`。
- 每个 `*Response` 均使用 `oneof outcome { <Result> result; BusinessError error; }`。

### 4.4 生成规则

- `scripts/generate_proto.py` 是唯一生成入口，固定 proto 根目录和输出目录，调用当前 lockfile 中的 `grpcio-tools`。
- 同时生成 `pb2.py`、`pb2.pyi`、`pb2_grpc.py`。
- 生成脚本只允许写 `src/rag_mvp/rpc/generated/`。
- `scripts/check_generated.py` 在临时目录重新生成并逐字节比较，发现未提交或手改生成物时失败。
- 开发环境可在 server 配置中启用 reflection；生产配置拒绝启用。若加入 `grpcio-reflection`，必须作为运行依赖并有配置测试。

### 4.5 先失败测试

1. 服务描述符精确包含上述 7 个 RPC，且只有 `SubmitDocument` 是 client streaming。
2. 所有 response 精确包含 `result/error` oneof。
3. `UploadDocumentRequest` 精确包含 `header/data` oneof。
4. `Evidence` 包含稳定 ID、正文、来源定位和四阶段分数字段，不含 answer/citation 字段。
5. `RequestContext` 只把 idempotency key 放在需要幂等的命令上；`GetJob/Retrieve` 只使用 request ID。
6. generated code 与 proto 同步检查初始失败，生成后通过。

预期初始失败原因：proto、生成脚本和 generated package 尚不存在。

### 4.6 最小实现

1. 先写 descriptor contract tests。
2. 创建完整 proto。
3. 创建确定性生成和差异检查脚本。
4. 生成 Python 代码并修正包内 import，使安装后的 `rag_mvp.rpc.generated` 可导入。
5. 在 CI 中执行 generated-code check。

### 4.7 验证命令

```powershell
uv run python scripts/generate_proto.py
uv run python scripts/check_generated.py
uv run pytest tests/contract/test_proto_contract.py tests/contract/test_generated_code.py
```

## 5. A3：目录与依赖边界

### 5.1 允许的依赖方向

```text
domain                         # 仅标准库和纯领域类型
application -> domain, ports
retrieval -> domain            # 本阶段为空包
ingestion -> application, ports
rpc -> application, bootstrap, generated
dev -> generated gRPC client
outbox -> application, ports
adapters -> domain, ports
bootstrap -> application, ports, adapters, rpc/ingestion/outbox factories
```

禁止：

- `domain/` 导入 gRPC、MySQL、NATS、Elasticsearch、Pydantic Settings 或任何 adapter。
- `application/` 导入 `adapters/`、protobuf DTO 或 concrete SDK。
- `rpc/rag_service.py`、`dev/cli.py`、`retrieval/` 导入 `adapters/`。
- 除 `bootstrap/container.py` 外的文件实例化 concrete adapter。
- import-time 读取 Settings 单例、建立连接、启动线程或注册全局 client。

### 5.2 端口名称骨架

`ports/` 只固定 SPEC 已确定的能力名称：

- `MetadataRepository`
- `ObjectStorage`
- `TaskQueue`
- `SearchEngine`
- `ModelGateway`
- `Parser`
- `Chunker`

本阶段可定义方法签名所需的最小 DTO，但不得为尚未实现的数据库事务发明半成品语义。涉及 Task/Outbox 原子写入、条件认领、active-version 复核的方法留到 Milestone B 的详细计划中，在领域模型与最终 schema 同步落地。

### 5.3 先失败测试

`tests/unit/test_import_boundaries.py` 使用 Python AST 检查源码 import，不依赖额外架构测试库：

1. domain 出现外层依赖时失败。
2. application 导入 adapters/rpc/generated 时失败。
3. rpc、dev、retrieval 越级导入 adapters 时失败。
4. concrete adapter 在 container 外被构造时失败（本阶段 adapters 为空，因此先固定扫描规则）。
5. 导入每个顶层 package 不产生网络连接或后台任务。

预期初始失败原因：目标包与边界测试尚不存在。

### 5.4 验证命令

```powershell
uv run pytest tests/unit/test_import_boundaries.py
uv run mypy src
```

## 6. A4：本地依赖与进程骨架

### 6.1 Settings

`src/rag_mvp/config.py` 使用 `pydantic-settings`，至少定义：

- `environment`：`development/test/production`
- gRPC host、port、graceful shutdown timeout、reflection 开关
- MySQL DSN
- Elasticsearch URL
- NATS URL、stream 名、consumer 名、subject
- local object root
- log level 与 JSON 日志开关

约束：

- Settings 只能由入口或 `bootstrap/container.py` 显式构造并传递。
- 测试默认值只能在 test factory 中提供；生产配置不得默默使用弱口令。
- 任何模型 API key 都只在 `.env.example` 中留空占位，且本阶段不实例化模型 client。

### 6.2 进程接口

- `main.py` 调用 `rpc.server.serve(settings, container)`。
- `ingestion/worker.py` 暴露 `run_worker(settings, container, stop_event)`；本阶段只等待停止事件，不消费消息。
- `outbox/main.py` 暴露 `run_outbox(settings, container, stop_event)`，组合空 `finalizer` 与 `relay` 生命周期；两者不发布、不消费、不 ACK/NAK。
- `bootstrap/container.py` 提供显式 `build_container(settings)` 与异步 `close()`；本阶段只返回无 concrete adapter 的工程基线容器。
- `rpc/rag_service.py` 只完成 protobuf response 构造，对所有尚未开放能力返回 `FEATURE_NOT_AVAILABLE`，不导入 adapters。
- `rpc/server.py` 负责创建 grpc aio server、注册 servicer、可选开发 reflection、启动、等待终止和 grace stop。
- `dev/cli.py` 只使用 generated stub 和 channel；本阶段提供连接/健康诊断或显示功能未开放，不直接调用 application/bootstrap/adapters。

入口命令固定为：

```text
rag-server  -> rag_mvp.main:main
rag-worker  -> rag_mvp.ingestion.worker:main
rag-outbox  -> rag_mvp.outbox.main:main
rag-dev     -> rag_mvp.dev.cli:main
```

### 6.3 Compose

`docker-compose.yml` 固定服务名：

- `mysql`：MySQL 8.x、InnoDB、持久 volume、`mysqladmin ping` healthcheck。
- `elasticsearch`：Elasticsearch 8.x 单节点、启用 dense vector、持久 volume、HTTP cluster-health healthcheck。
- `nats`：启用 JetStream（`-js`）、持久 volume、HTTP monitoring healthcheck。
- `rag-server`、`rag-worker`、`rag-outbox`：使用同一 Python 项目镜像/命令，通过 `depends_on: condition: service_healthy` 等待依赖。

镜像使用明确的版本 tag，不使用 `latest`。Compose 不创建第二套数据库、搜索引擎或队列；不加入 Redis、Qdrant、SQLite、FastAPI 或 Nginx。

`.dockerignore` 必须排除 `.git/`、虚拟环境、缓存、`.env`、`references/`、`data/`、日志和测试产物，避免本地参考材料、密钥或运行数据进入 Docker 构建上下文。

应用容器构建如需 `Dockerfile`，应在本工作包内将其加入目标文件树并记录原因；首选在计划实施时使用一个最小 multi-stage `Dockerfile`，由 `uv.lock` 冻结依赖，三种应用服务复用同一镜像。

### 6.4 先失败测试

1. Settings 构造不触发连接；`build_container` 不在 import-time 执行。
2. 三个进程收到 stop event 后在超时内退出，资源 `close()` 只执行一次。
3. gRPC 对 7 个尚未开放的 RPC 返回 response oneof 的 `FEATURE_NOT_AVAILABLE`，不返回 `UNIMPLEMENTED`。
4. production 开启 reflection 时配置校验失败；development 可显式启用。
5. `docker compose config` 能解析，服务名、healthcheck、JetStream 参数和持久 volume 存在。

预期初始失败原因：Settings、入口、空容器和 Compose 尚不存在。

### 6.5 验证命令

```powershell
uv run pytest tests/unit/test_config.py tests/unit/test_process_lifecycle.py
docker compose config --quiet
docker compose up -d mysql elasticsearch nats
docker compose ps
docker compose down
```

说明：只执行 `docker compose down`，不使用 `down -v`，避免删除持久 volume。是否实际启动 Compose 取决于本机 Docker 可用性；未运行时必须在交接中明确说明。

## 7. A5：本地 hook 与基础 CI

### 7.1 pre-commit hook

`.githooks/pre-commit` 使用 Git for Windows 可执行的 POSIX `sh`，按顺序运行：

```sh
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run mypy src scripts
uv run python scripts/check_generated.py
uv run pytest tests/unit tests/contract
```

hook 只检查，不改写工作区；失败保留暂存区。README 记录一次性配置命令：

```powershell
git config core.hooksPath .githooks
```

执行该 Git 配置会改变当前 clone，因此只记录命令，不在未获用户授权时自动执行。

### 7.2 GitHub Actions

`.github/workflows/quality.yml`：

1. checkout。
2. 安装规范指定的 Python 3.12 和 `uv`。
3. `uv sync --frozen --group dev`。
4. 运行与 hook 相同的 Ruff、format、mypy、generated-code、unit/contract 检查。
5. 缓存只能包含依赖下载缓存，不提交虚拟环境、测试结果或日志。

Milestone A 不在 CI 启动 MySQL、Elasticsearch、NATS，也不声称 integration/E2E/resilience 已通过；这些 job 随 B～D 增加。

### 7.3 先失败测试/验证

1. 本地逐条运行 hook 中的命令。
2. 使用 shell 语法检查或 Git for Windows `sh -n .githooks/pre-commit`。
3. 检查 workflow 只引用 lockfile 安装并与 hook 命令一致。

验证命令：

```powershell
sh -n .githooks/pre-commit
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run mypy src scripts
uv run python scripts/check_generated.py
uv run pytest tests/unit tests/contract
```

## 8. 实施顺序与复选框

每项严格执行“失败测试 → 最小实现 → 通过验证”。Docker/CI 发布出口仍待验收，但可按 `docs/PLAN.md` 2.1 继续 Mock Functional 模块：

- [x] A1.1 创建 `pyproject.toml`、基础测试目录和预期失败的包/config 测试。
- [x] A1.2 创建包、Settings、README、ignore、license，并生成 `uv.lock`。
- [x] A1.3 运行 Ruff、format、mypy、unit/contract 基线。
- [x] A2.1 创建预期失败的 protobuf descriptor 与 generated-code contract tests。
- [x] A2.2 创建完整 `rag_service.proto` 和生成脚本。
- [x] A2.3 生成并验证 Python protobuf 代码无 diff。
- [x] A3.1 创建预期失败的 import-boundary 测试。
- [x] A3.2 创建最终包目录、最小 application DTO 与 ports 骨架。
- [x] A3.3 验证所有层级 import 规则。
- [x] A4.1 创建预期失败的 Settings、进程生命周期和空 RPC 测试。
- [x] A4.2 实现 container、gRPC Server、Worker、Outbox 与 dev client 骨架。
- [x] A4.3 创建 Dockerfile/Compose（若实施时确认应用容器启动需要 Dockerfile）并验证配置。
- [ ] A4.4 在 Docker 可用时启动依赖、检查 healthcheck、再无损停止。
- [x] A5.1 创建 pre-commit hook 和基础 CI。
- [x] A5.2 运行全部快速门禁并记录真实结果。
- [x] A5.3 检查 `git status --short`，列出本 Milestone 文件、未跟踪参考目录和未运行项。

实施验证记录（2026-08-24）：当前 Windows 环境可用 `uv` 与 Python，但没有 Docker。Git for Windows 内置的 `sh.exe` 未加入 PowerShell PATH，定位后已完成 `sh -n .githooks/pre-commit` 并实际执行整个 hook；hook 与 GitHub Actions 命令经静态比对一致。Compose YAML 与服务拓扑经独立 YAML 解析通过。A4.4 和真实 GitHub Actions 运行留待具备对应环境时验收。

## 9. 建议提交边界

以下仅为建议；未经用户明确授权，不执行 `git add`、`git commit` 或 `git push`。

1. `build(deps): 建立 Python 工程与质量配置`
   - `pyproject.toml`、`uv.lock`、基础包、`.gitignore`、`.env.example`、README、LICENSE、A1 测试。
2. `feat(rpc): 定义完整 RAG gRPC 契约`
   - proto、生成脚本、generated code、proto contract tests。
3. `refactor: 建立分层目录与依赖边界`
   - application/domain/ports/adapters/retrieval 包骨架与 import-boundary tests。
4. `build: 增加服务进程与本地依赖骨架`
   - config、bootstrap、rpc server、worker、outbox、dev CLI、Dockerfile/Compose、生命周期测试。
5. `ci: 增加本地质量门禁与基础 CI`
   - `.githooks/pre-commit`、`.github/workflows/quality.yml` 及相关说明。

每个建议提交必须只暂存对应文件，并在交接中列出实际运行命令和未运行的 Compose/CI 项目。

## 10. Milestone A 验收清单

- [x] `uv sync --frozen --group dev` 成功。
- [x] `uv run ruff check src tests scripts` 成功。
- [x] `uv run ruff format --check src tests scripts` 成功。
- [x] `uv run mypy src scripts` 成功。
- [x] `uv run python scripts/check_generated.py` 成功。
- [x] `uv run pytest tests/unit tests/contract` 成功（19 passed）。
- [x] proto 描述符包含完整 7 RPC，generated code 可重复生成且无 diff。
- [x] import-boundary tests 阻止 domain/application/rpc/dev/retrieval 越层依赖。
- [x] 导入源码不建立 MySQL、ES、NATS 或模型连接。
- [x] 三个进程入口的核心生命周期可启动并优雅退出。
- [x] 开发环境可显式启用 gRPC reflection，生产环境禁止。
- [ ] `docker compose config --quiet` 成功。
- [ ] 在 Docker 可用时，MySQL、Elasticsearch、NATS healthcheck 成功；未运行则明确记录。
- [x] 空 RPC 使用 `BusinessError(FEATURE_NOT_AVAILABLE)`，不使用 HTTP adapter 或 gRPC `UNIMPLEMENTED`。
- [x] pre-commit 与 CI 快速检查命令一致。
- [x] `git status --short` 已检查；`references/` 未读取、未修改、未暂存。
- [x] 未实现或暗中引入 Milestone B～E 行为。

本地实现门禁通过后，可依据真实代码结构编写 `docs/plans/milestone-b-internal-alpha.md`；Docker/CI 清单未完成前，Milestone A 不得标记为正式阶段出口已通过。
