# RAG MVP 交付路线图

> 本文件是跨阶段路线图，不直接作为逐文件施工脚本。执行某个 Milestone 前，必须为该 Milestone 创建独立实施计划，列出准确文件、接口、失败测试、最小实现、验证命令和建议提交边界，并使用复选框跟踪。

**目标：** 用可独立验收的增量交付 Python RAG 服务，从工程骨架、内部 Alpha、可试用 MVP 演进到可靠发布基线，最后接入 Go 产品控制面。

**架构：** Python 始终拥有 RAG 执行域写入权并只提供私网 gRPC；MySQL 保存事实状态，NATS JetStream 调度 Task，Elasticsearch 提供 Dense KNN 与 BM25 检索。每个 Milestone 只缩小功能范围，不引入另一套临时架构。

**技术栈：** Python 3.12+、uv、gRPC/Protocol Buffers、MySQL 8/InnoDB、Elasticsearch、NATS JetStream、Ruff、mypy、pytest、Docker Compose；后续 Go 控制面使用 Go module 与同一份 protobuf。

**权威规格：** [`SPEC.md`](SPEC.md)；仓库协作约束见 [`AGENTS.md`](AGENTS.md)。

## 1. 文档层次与编号规则

```text
SPEC.md
  └─ 定义最终架构、领域语义、RPC 契约、状态机和测试不变量

PLAN.md
  └─ 定义交付顺序、Milestone、工作包、依赖和阶段出口

plans/milestone-<x>-*.md
  └─ 执行前创建；定义逐文件、逐测试、逐提交的实施步骤
```

- SPEC 中继续使用 `Phase 0～4` 和 `P0-1～P4-4`，其编号不变。
- PLAN 只使用 `Milestone A～E` 和 `A1～E4`，避免两套 Phase 同名但含义不同。
- Milestone 不是对 SPEC 要求的删减；它只说明何时实现。已交付行为必须满足其涉及的 SPEC 语义。
- 详细实施计划必须引用对应 SPEC 条款和本路线图工作包，不得自行发明第三套架构。

## 2. 全局约束

- Python 只能提供版本化 gRPC；不得新增 FastAPI/HTTP 业务 adapter。
- MySQL、Elasticsearch、NATS JetStream 是唯一元数据、检索和异步任务基础设施；不得引入 SQLite、Qdrant 或数据库轮询队列。
- 完整 `RagService` proto 从 Milestone A 起一次定义；暂未开放的领域能力返回 `BusinessError(code="FEATURE_NOT_AVAILABLE")`，不得用 gRPC `UNIMPLEMENTED` 表达可预期业务状态。
- 所有 Task 与 OutboxEvent 在同一 MySQL 事务创建；Worker 是唯一消费 NATS 并执行 ACK/NAK 的进程。
- 核心 schema 从首次落库起采用最终实体和关键字段，避免后续为可靠性能力重写主表、ES `_id` 或消息格式。
- 每项实现先写失败测试，再做最小实现；实际运行和未运行的检查必须记录。
- 路线图只提供建议提交边界。未经用户明确授权，不执行 `git commit` 或 `git push`。

## 3. 里程碑总览

| Milestone | 交付物 | 使用范围 | 对应 SPEC |
|---|---|---|---|
| A：工程基线 | 可生成、检查、测试和启动依赖的仓库骨架 | 开发环境 | Phase 0、P0-1～P0-3 |
| B：内部 Alpha | TXT 异步摄取、Dense 检索和 evidence 闭环 | 内部验证 | P1-1～P1-5、P2-1～P2-2、P3-1～P3-3 的最小纵向切片 |
| C：纯 RAG MVP | 四类文件、混合检索、基础删除/重试和评测 | 单机受控试用 | P1-3、P2-1～P2-4、P3-1～P3-4 |
| D：可靠发布基线 | 完整状态机、并发栅栏、故障恢复和发布门禁 | 单机生产试运行 | Phase 1～3 的全部可靠性要求、T1～T25 |
| E：Go 产品控制面 | 公网 API、会话、Chat、SSE 和 Agent Harness | 产品演进 | Phase 4、P4-1～P4-4 |

```text
Milestone A
    ↓
Milestone B
    ↓
Milestone C  ← 纯 RAG MVP，可受控试用
    ↓
Milestone D  ← 满足 SPEC 发布可靠性要求
    ↓
Milestone E  ← Go 公网产品面与 Agent
```

## 4. RPC 能力开放矩阵

完整 proto 在 Milestone A 固定，行为按 Milestone 开放：

| RPC | A | B | C | D | E 的调用方 |
|---|---|---|---|---|---|
| `CreateDataset` | 契约 | 可用 | 可用 | 完整可靠性 | Go Dataset 服务 |
| `SubmitDocument` | 契约 | TXT | 四类文件 | 重建/并发/恢复 | Go Document 服务 |
| `GetJob` | 契约 | 可用 | 可用 | 完整错误与重试状态 | Go 状态 API |
| `Retrieve` | 契约 | Dense | Dense + BM25 + RRF + 可选 Rerank | 完整过滤/可见性复核 | Go RAG Tool |
| `RetryJob` | 契约 | `FEATURE_NOT_AVAILABLE` | 基础合规重试 | 并发唯一与上限完整 | Go Document 服务 |
| `CancelJob` | 契约 | `FEATURE_NOT_AVAILABLE` | `FEATURE_NOT_AVAILABLE` | 完整 checkpoint 收敛 | Go Document 服务 |
| `DeleteDocument` | 契约 | `FEATURE_NOT_AVAILABLE` | 基础逻辑删除与异步清理 | 完整 generation fence/竞态 | Go Document 服务 |

这里的“基础”仍必须符合 SPEC：`RetryJob` 创建新 Job/Task/Outbox，不复活旧 Job；`DeleteDocument` 先逻辑删除，再通过 Cleanup Task 删除 ES 与对象。Milestone D 补充并发、强杀和极端投递窗口，不改变已开放 RPC 的外部语义。

## 5. Milestone A：工程基线

**目的：** 建立可重复开发环境、完整契约和快速质量反馈，不实现业务流程。

### A1：Python 工程与质量配置

- 创建 `pyproject.toml`、`uv.lock`、`src/rag_mvp/`、`tests/unit/`、`tests/contract/`、`tests/integration/`。
- 配置 Ruff、mypy、pytest、pytest-asyncio、pytest-cov，以及包导入 smoke test。
- 创建 `.gitignore`、`.env.example`、README 开发命令和 Apache-2.0 `LICENSE`；不提交密钥、`data/`、缓存或日志。

### A2：完整 protobuf 契约

- 一次定义 SPEC 列出的 7 个 RPC、RequestContext、流式上传 oneof、统一 Result/BusinessError oneof。
- 建立 Python generated code 命令和“生成后无 diff”契约检查。
- 只允许开发环境启用 Server Reflection。

### A3：目录与依赖边界

- 建立 `domain/application/ports/adapters/rpc/ingestion/retrieval/outbox/bootstrap/dev`。
- 增加 import-boundary 测试：domain 不依赖 SDK，application 只依赖 ports，bootstrap 是 concrete adapter 唯一装配点。
- Settings 不得在 import-time 建立 MySQL、ES、NATS 或模型连接。

### A4：本地依赖与进程骨架

- Compose 启动 MySQL、Elasticsearch、NATS JetStream，并提供 healthcheck。
- 建立 gRPC Server、Worker、Outbox 三个空进程入口及优雅退出 smoke test。
- 固定开发环境服务名、端口、volume 和 NATS stream/consumer 命名。

### A5：本地 hook 与基础 CI

- `.githooks/pre-commit` 运行 Ruff check、Ruff format check、mypy、unit/contract。
- GitHub Actions 从第一阶段起运行同一组检查；后续 Milestone 只扩展 job，不另建平行 CI。
- Hook 失败阻止本地提交；CI 防止未配置 hook 或 `--no-verify` 绕过。

**阶段出口：** 质量命令全部成功；proto 可重复生成；三个应用入口可启动后退出；MySQL/ES/NATS healthcheck 成功；CI 在文档或 smoke-test PR 上实际运行。

## 6. Milestone B：最小异步检索闭环（内部 Alpha）

**目的：** 用最终数据主线完成 TXT 上传到 evidence 返回的纵向切片。

### B1：核心领域模型与最终主 schema

- 建立 `Tenant`、`Dataset`、`Document`、`IngestionFingerprint`、`Job`、`Task`、`OutboxEvent`、`IndexBuild`、`Chunk manifest`、`Evidence`；MVP 由服务端注入固定 `default_tenant`，客户端不能任意指定 tenant。
- 从第一版包含 `index_version`、`active_version`、`next_index_version`、`lifecycle_generation`、`document_generation`、`last_delivery_sequence`。
- 建立状态转换、唯一约束、`file_sha256/config_digest/content_sha256` 和 RAGFlow xxHash64 `chunk_id` 单测。

### B2：端口与基础适配器契约

- 定义 MetadataRepository、ObjectStorage、TaskQueue、SearchEngine、ModelGateway、Parser、Chunker。
- 实现 MySQL、local storage、NATS JetStream、Elasticsearch、OpenAI-compatible adapter；CI 使用 deterministic FakeModelGateway。
- 每个 adapter 运行同一语义的 contract test，真实基础设施测试使用 integration marker。

### B3：TXT staging 与 Outbox 投递

- gRPC 流上传写入由 `idempotency_key` 派生的 staging object，并校验大小与 SHA-256。
- MySQL 事务原子创建 Document/Job/Task/Fingerprint/`WAITING_OBJECT` Outbox。
- Finalizer 仅在正式对象可读且条件匹配时置 READY；Relay 只发布 READY 的 `task_id`。
- Finalizer 超过上限必须收敛到 `FAILED(OBJECT_FINALIZATION_FAILED)`，不能永久 PENDING。

### B4：Worker 与 TXT Pipeline

- Worker 只消费 `task_id`，从 MySQL 条件认领 Task，执行 parse → normalize → chunk → embed → index。
- TXT Parser 和 Recursive Chunker 输出稳定 locator、ordinal、content hashes。
- ES 物理 `_id` 从第一版使用 `document_id:index_version:chunk_id`；首次索引也通过 IndexBuild 激活。
- MySQL 终态持久化成功后才 ACK；已终态 delivery 只 ACK，不重复 parser/embedder。

### B5：Dense Retrieve 与 gRPC 纵向闭环

- 开放 `CreateDataset`、`SubmitDocument`、`GetJob`、`Retrieve`。
- RetrievalService 从 ES Dense 召回后，用 MySQL 复核 Document 未删除且命中属于 active version。
- evidence 返回 `chunk_id/document_id/content_with_weight/source_name/locator` 和各阶段分数。
- 其余 RPC 返回 `BusinessError(FEATURE_NOT_AVAILABLE)`。

### B6：Alpha 可观测性与验证

- 输出包含 `request_id/job_id/document_id/dataset_id/stage/duration_ms/index_version/error_code` 的结构化日志。
- 验证相同 idempotency key 复用、不同 key 相同内容复用 canonical Job、Finalizer 发布门、ACK 丢失重投和 chunk upsert 幂等。
- Compose E2E：上传 TXT，轮询 Job 成功，通过 gRPC Retrieve 获得 evidence。

**阶段出口：** TXT 的 upload → async ingest → Dense retrieve 全链路成功；失败 Finalizer 不挂死；重复提交和重复 delivery 不产生第二份可见索引。该版本仅限内部验证。

## 7. Milestone C：纯 RAG MVP（单机受控试用）

**目的：** 补齐实际 RAG 使用面，并提供数据删除和失败恢复的基本闭环。

### C1：多格式解析与精确 locator

- 增加 Markdown、代码和文本 PDF Parser；保留页码、行号、代码语言、符号等 provenance。
- 建立四类文件的 golden chunks，固定 normalize、chunk boundary、content hash 和 locator。
- 不支持的文件类型返回稳定 BusinessError，不进入未定义解析路径。

### C2：混合检索

- Elasticsearch adapter 分别返回 Dense 和 BM25 候选，不在 adapter 内融合。
- `retrieval/hybrid.py` 唯一执行 RRF、去重和稳定排序；metadata filter 必须包含 dataset 边界。
- 固定 fixture 验证 dense/sparse/fusion 分数和排序可重复。

### C3：Rerank、ContextPlan 与 Evidence

- application 调用 ModelGateway.rerank，纯函数 `retrieval/rerank.py` 只接受分数并排序。
- 模型不可用时降级到 RRF，不把整个 Retrieve 请求变成不可用。
- ContextBuilder 按 token budget 保留完整 evidence；provenance 规范化来源但不分配 `[n]`。

### C4：基础 RetryJob

- 对 `FAILED && retryable=true` 的 Job 创建新 Job/Task/READY Outbox，旧 Job 保持 FAILED。
- 重试只复用已存在的正式 object；没有正式 object 的失败要求使用新 idempotency key 重新上传。
- 重复相同 Retry 请求按 idempotency key 返回同一 retry Job；完整并发唯一约束压力测试留到 D2。

### C5：基础 DeleteDocument

- MySQL 事务先将 Document 标记 DELETED，使 Retrieve 立即不可见。
- 创建 `DELETE_DOCUMENT` Job、`CLEANUP_DOCUMENT` Task、READY Outbox；清理 ES 全文档版本和正式对象。
- 同一 `idempotency_key` 的 Delete 重放返回第一次的 Job/result；使用新请求删除已删除文档时返回稳定 `DOCUMENT_ALREADY_DELETED` BusinessError。Delete/Finalizer、Delete/Worker 极端竞态留到 D4。

### C6：评测与 MVP 验收

- Dataset 第一个 READY Document 后冻结 embedding model/dimension。
- 建立至少 30 个问题的评测集，验证 Recall@6、MRR@6 和 evidence locator。
- CI 增加 MySQL/ES/NATS integration、gRPC contract 和四类文件 E2E。

**阶段出口：** 四类文件可上传、查询 Job、混合检索、重排并返回 evidence；失败任务可合规重试，文档可立即逻辑删除并最终清理；评测达到 SPEC 初始阈值。该版本可用于单机、受控内部数据试用，不宣称完成全部并发和强杀恢复保证。

## 8. Milestone D：可靠发布基线

**目的：** 补齐所有状态、投递、版本和并发不变量，通过 SPEC T1～T25。

### D1：Outbox、Finalizer 与投递恢复

- 完整 staging sweeper 引用保护、Finalizer 指数退避/终态补偿、Relay publish-confirm 恢复。
- 覆盖 Finalizer 强杀、Relay 发布后标记前强杀、NATS 暂时不可用和 MAX_DELIVERIES advisory 补偿。
- 对应重点测试：T2、T3、T4、T12、T14、T15、T18。

### D2：Fingerprint 与 Retry 并发一致性

- 完整 `PENDING/RUNNING/SUCCEEDED/FAILED_RETRYABLE/RELEASED` 转换。
- 对 canonical upload 使用唯一 fingerprint + 行锁；对 Retry 使用原 Job 行锁、活跃子 Job 唯一约束和 `max_user_retries`。
- 对应重点测试：T1、T21、T24、T25。

### D3：索引版本与原子可见性

- Document 行锁分配唯一 `next_index_version`，IndexBuild 经 BUILDING → ACTIVE/ABANDONED。
- generation 匹配时才切换 active version；失败版本创建 `CLEANUP_INDEX_VERSION` 系统 Job。
- 对应重点测试：T8、T11、T17，以及 ES `_id`/manifest 幂等集成测试。

### D4：Cancel、Delete 与 generation fence

- 开放 CancelJob：PENDING 撤销未发布 Outbox，RUNNING 写 cancel request 并在 checkpoint 收敛。
- Delete 在 Document 行锁内递增 lifecycle generation、释放 fingerprint、取消未终态摄取和未发布 Outbox。
- Worker 认领和完成均条件验证；失配只能 ACK/取消/清理，绝不重新激活 Document。
- 对应重点测试：T5、T10、T13、T16、T19、T20、T22、T23。

### D5：发布验收、CI 与恢复演练

- 完成 T1～T25、覆盖率门槛、四类 E2E、固定 eval，以及 Docker `KILL` Worker 恢复演练。
- PR 必跑 unit/contract/integration/E2E/resilience；夜间运行 eval 和耗时恢复测试。
- 对照 SPEC 附录 A 逐项验收，未达到的项目必须阻塞发布声明。

**阶段出口：** SPEC T1～T25、断电恢复、覆盖率和附录 A 全部通过；可以作为单机生产试运行版本。Kubernetes、多租户和多实例高可用仍属于后续工作。

## 9. Milestone E：Go 产品控制面与 Agent

**目的：** 让 Go 成为唯一公网入口，在不侵入 Python RAG 执行域的前提下提供 Chat 与 Agent。

### E1：固定 monorepo 目标结构

```text
RAG/
├─ proto/rag/v1/                 # Python/Go 唯一 RPC 契约
├─ services/rag-python/          # Python RAG gRPC、Worker、Outbox
├─ apps/go-api/                  # Go 公网 API、会话、Chat、Agent
├─ deploy/                       # Compose；后续 Helm/Kubernetes
├─ docs/
└─ .githooks/
```

- 迁移必须保持 Python package、generated code 和 Compose 路径可重复构建。
- Go 不直接写 Python 所有的 Document/Job/Task/索引状态表。

### E2：Go gRPC Client 与产品 API

- 从共享 proto 生成 Go client，并运行 Python/Go contract compatibility tests。
- Go 实现认证、真实 tenant 映射、会话、限流和文档/Job 公网 API。
- 外部请求的 tenant/request/idempotency context 由 Go 校验后传给 Python。

### E3：Chat、SSE 与 Agent Harness

- Agent 将 Python `Retrieve` 作为 RAG Tool，自行决定调用时机。
- Go 根据 evidence 构建 Prompt、分配 `[1]...[N]` Citation、调用 Chat Model。
- Go 向客户端发送 retrieval/token/final/error SSE；Python 不生成答案或 SSE。

### E4：Go 质量与多实例验证

- 本地 hook 和 CI 增加 `gofmt -l`、`go vet ./...`、`go test ./...`。
- 验证多个 Go API 和 Python Worker 实例下的 NATS redelivery、幂等和 gRPC deadline。
- Kubernetes/Helm 与基础设施高可用在单机产品闭环稳定后另建实施计划。

**阶段出口：** 公网客户端只访问 Go；Go 可上传/查询/检索并输出带 citation 的流式回答；Python 仍是 RAG 执行状态唯一写入方。

## 10. 测试不变量分配

| 首次建立 | SPEC 测试 | 后续强化 |
|---|---|---|
| B | T1、T2、T4、T9、T11、T12、T15、T18 的基础路径 | D1/D2/D3 加入并发与强杀窗口 |
| C | T5、T6、T7、T13、T25 的基础路径 | D2/D4 完整竞态和释放语义 |
| D1 | T2、T3、T4、T12、T14、T15、T18 | 发布恢复演练 |
| D2 | T1、T21、T24、T25 | 高并发重复请求 |
| D3 | T8、T11、T17 | ES/MySQL 可见性窗口 |
| D4 | T5、T10、T13、T16、T19、T20、T22、T23 | Delete/Cancel/Worker/Finalizer 竞态 |
| D5 | T1～T25 全集 | 覆盖率、E2E、eval、Docker KILL |

测试允许在较早 Milestone 建立基础场景，但对应 D 子阶段必须再次覆盖 SPEC 定义的完整并发和故障窗口。

## 11. 详细实施计划规则

每个 Milestone 开始前，在 `plans/` 创建一个独立文件：

```text
plans/
├─ milestone-a-engineering-baseline.md
├─ milestone-b-internal-alpha.md
├─ milestone-c-rag-mvp.md
├─ milestone-d-reliability.md
└─ milestone-e-go-control-plane.md
```

每份详细计划必须包含：

1. 创建、修改和测试的准确文件路径；
2. 任务消费与产出的准确接口/类型；
3. 先失败测试、预期失败原因、最小实现和通过命令；
4. 独立可验收的任务边界；
5. 建议 `git add` 文件和 Conventional Commit 消息；
6. 明确说明只有用户授权后才实际提交。

不要一次写完五份详细施工计划。完成并验收当前 Milestone 后，再结合真实代码结构编写下一份，避免后续计划基于尚不存在的实现细节失真。

## 12. 当前起点

`plans/milestone-a-engineering-baseline.md` 已验收并实施：Python 工程、完整 gRPC 契约、分层包、三个进程骨架、Compose、pre-commit 与基础 CI 已落地，unit/contract 快速门禁通过。当前环境没有 Docker，因此 MySQL、Elasticsearch、NATS healthcheck 和真实 GitHub Actions 尚未验收，Milestone A 仍未达到阶段出口。下一步是在具备 Docker 的环境完成剩余验收；Milestone A 全部通过前不实施 B～E。
