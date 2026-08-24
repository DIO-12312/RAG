# Milestone B：Mock Functional 异步检索闭环实施计划

> 状态：Mock Functional 已完成；真实基础设施验收延期
> 对应路线图：`docs/PLAN.md` B1～B6、2.1 无 Docker开发通道
> 对应规格：`docs/SPEC.md` 2.1～2.5、4.1～4.7、5.2～5.7、P1-1～P1-5、P2-1～P2-2、P3-1～P3-3
> 范围：用真实 gRPC/application/Outbox/Worker/pipeline/retrieval 调用链和测试专用 Fake ports 跑通 TXT upload → async ingest → Dense retrieve。真实 MySQL/Elasticsearch/NATS adapter 与 Compose 验收延期，不据此声明内部 Alpha 发布出口通过。

## 1. 不变量与边界

- Python 仍只提供 `rag.v1.RagService` gRPC；不新增 HTTP/FastAPI。
- 生产代码只依赖 `ports/`；Fake 只能位于 `tests/fakes/`，`bootstrap/container.py` 禁止导入它们。
- FakeMetadataRepository 是 MySQL 语义的测试替身，不得使用 SQLite；FakeSearchEngine 不是生产搜索引擎；FakeTaskQueue 不是生产消息系统。
- Task 与 OutboxEvent 必须由 Repository 的单个原子操作创建。首个摄取 Outbox 为 `WAITING_OBJECT`，只有 Finalizer 可转 READY；Relay 只发布 READY 的 `task_id`。
- Worker 是唯一调用 `consume/ack/nak` 的代码；Outbox 只能 publish。
- NATS 消息语义只含 `task_id`；Worker 必须从 MetadataRepository 重新读取和条件认领。
- Job/Task 状态固定为 `PENDING → RUNNING → SUCCEEDED | FAILED | CANCELLED`，FAILED 不复活。
- ES 逻辑记录 ID 从第一版固定为 `{document_id}:{index_version}:{chunk_id}`；`chunk_id` 严格使用 RAGFlow xxHash64 规则。
- B 只开放 `CreateDataset/SubmitDocument/GetJob/Retrieve`；Retry/Cancel/Delete 继续返回 `FEATURE_NOT_AVAILABLE`。
- 本 Milestone 每个 B1～B6 工作包完成并通过相称检查后立即独立 commit，不执行 push。

## 2. B1：领域模型、状态机与稳定 ID

### 文件

```text
src/rag_mvp/domain/
├─ enums.py
├─ errors.py
├─ ids.py
├─ models.py
└─ policies.py
tests/unit/domain/
├─ test_ids.py
├─ test_models.py
└─ test_state_machines.py
```

### 类型

- `DocumentStatus(PENDING, READY, FAILED, DELETED)`
- `JobStatus/TaskStatus(PENDING, RUNNING, SUCCEEDED, FAILED, CANCELLED)`
- `JobType(INGEST_DOCUMENT, DELETE_DOCUMENT, CLEANUP_INDEX_VERSION)`
- `TaskType(INGEST_DOCUMENT, CLEANUP_DOCUMENT, CLEANUP_INDEX_VERSION)`
- `OutboxStatus(WAITING_OBJECT, READY_TO_PUBLISH, PUBLISHED, CANCELLED)`
- `FingerprintState(PENDING, RUNNING, SUCCEEDED, FAILED_RETRYABLE, RELEASED)`
- `IndexBuildStatus(BUILDING, ACTIVE, ABANDONED)`
- immutable dataclasses：`Dataset/Document/IngestionFingerprint/Job/Task/OutboxEvent/IndexBuild/Chunk/Evidence/Locator/ScoreBreakdown`。

稳定规则：

```text
document_id = UUIDv7-compatible service-generated ID
config_digest = SHA256(canonical_json(parser_version/chunker_config/embedding_model))
file_sha256 = SHA256(raw upload bytes)
content_sha256 = SHA256(content_with_weight UTF-8 bytes)
chunk_id = xxh64((content_with_weight + document_id).encode("utf-8", "surrogatepass"))
es_record_id = document_id:index_version:chunk_id
```

### 先失败测试

- 固定 fixture 验证 canonical JSON、三个 SHA-256 和 xxHash64。
- 相同文档/正文 ID 稳定；正文或 document ID 改变则 ID 改变。
- 只允许定义的状态迁移；终态不能回到 PENDING/RUNNING。
- dataclass 验证进度范围、非空 ID、index version 与 delivery sequence。

### 验证与提交

```powershell
uv run pytest tests/unit/domain
uv run mypy src
uv run ruff check src tests
```

提交：`feat(domain): 建立 RAG 核心模型与状态规则`

## 3. B2：Ports 与测试专用 Fake 契约

### 文件

```text
src/rag_mvp/ports/
├─ metadata.py
├─ storage.py
├─ message_queue.py
├─ search_engine.py
├─ model.py
├─ parser.py
└─ chunker.py
tests/fakes/
├─ metadata.py
├─ storage.py
├─ task_queue.py
├─ search_engine.py
├─ model.py
├─ parser.py
└─ clock.py
tests/contract/
├─ test_metadata_repository_contract.py
├─ test_object_storage_contract.py
├─ test_task_queue_contract.py
├─ test_search_engine_contract.py
└─ test_model_gateway_contract.py
```

### Port 能力

- `MetadataRepository`：create dataset、原子 submit、job 查询、Finalizer 条件转换、Outbox READY 读取/发布标记、Task 条件认领/完成/失败、active-version 批量复核。
- `ObjectStorage`：write/read/delete staging、幂等 promote final、exists。
- `TaskQueue`：publish、consume、ack、nak；`Delivery` 含 task_id/delivery_sequence/redelivery_count。
- `SearchEngine`：upsert chunks、dense/sparse candidates、delete version/document。
- `ModelGateway`：embed batch、rerank。
- `Parser/Chunker`：输入输出都使用 domain/application DTO，不接触 protobuf。

### Fake 语义

- FakeMetadataRepository 使用 `asyncio.Lock` 保护复合状态变化，支持快照/故障注入和操作计数。
- FakeTaskQueue 显式维护 available/in-flight/acked，NAK 可重排，重复 publish 保留至少一次语义。
- FakeSearchEngine 以版本化 `es_record_id` 幂等 upsert，dense 分数确定；不在 adapter 内执行 RRF。
- FakeModelGateway 由文本 hash 生成固定维度向量，rerank 返回稳定分数。
- Fake 只能由测试 fixture 显式装配。

### 先失败测试

- 同一 contract suite 验证各 port 的最小语义。
- Task/Outbox 原子出现；Finalizer 条件不匹配不得 READY。
- 重复 publish/delivery/upsert 可观察但不破坏最终结果。
- Search 分别返回 Dense/Sparse candidates，不融合。

### 验证与提交

```powershell
uv run pytest tests/contract
uv run pytest tests/unit/test_import_boundaries.py
```

提交：`test(ports): 建立基础端口与确定性 Fake 契约`

## 4. B3：TXT staging、Fingerprint 与 Outbox 投递

### 文件

```text
src/rag_mvp/application/document_service.py
src/rag_mvp/application/dto.py
src/rag_mvp/adapters/storage/local.py
src/rag_mvp/outbox/finalizer.py
src/rag_mvp/outbox/relay.py
tests/unit/application/test_document_service.py
tests/unit/outbox/test_finalizer.py
tests/unit/outbox/test_relay.py
tests/contract/test_object_storage_contract.py
```

### 流程

1. `DocumentService` 验证 header/文件大小/SHA-256，把字节写入由 idempotency key 派生的 staging key。
2. Repository 原子锁定 `(dataset_id,file_sha256,config_digest)`；首次创建 Document/Job/Task/WAITING Outbox，重复提交返回 canonical Job。
3. 未被选中的 staging object 立即删除。
4. Finalizer 幂等 promote，且只有 `WAITING_OBJECT + Document 未删除` 才 READY。
5. Relay publish `task_id` 后标记 PUBLISHED；标记前崩溃造成的重复 publish 由 Worker 幂等处理。

### 测试

- 相同 idempotency key 返回同一结果。
- 不同 key 相同文件/配置复用 fingerprint canonical Job。
- Finalizer 前 Relay 不发布；promote 可重放。
- 事务失败不产生可见元数据，staging 可清理。
- publish 成功、mark 前故障后再次 Relay 产生重复 delivery。

### 验证与提交

```powershell
uv run pytest tests/unit/application/test_document_service.py tests/unit/outbox
uv run pytest tests/contract/test_object_storage_contract.py
```

提交：`feat(ingestion): 实现 staging 与 Outbox 投递主线`

## 5. B4：Worker 与 TXT Pipeline

### 文件

```text
src/rag_mvp/adapters/parsers/text.py
src/rag_mvp/adapters/chunkers/recursive.py
src/rag_mvp/ingestion/checkpoints.py
src/rag_mvp/ingestion/pipeline.py
src/rag_mvp/application/ingestion_service.py
src/rag_mvp/ingestion/worker.py
tests/unit/ingestion/
├─ test_text_parser.py
├─ test_recursive_chunker.py
├─ test_pipeline.py
└─ test_worker.py
tests/fixtures/golden_chunks/txt.json
tests/resilience/test_redelivery_idempotency.py
```

### 流程

- Worker consume delivery → Repository 条件认领 → IngestionService 执行一个 Task。
- pipeline：read final object → parse → normalize → chunk → embed → build versioned indexed chunks → SearchEngine upsert。
- Repository 完成事务验证 Task/Job/Document generation 后写 manifest、激活 version、标记成功；成功后 Worker ACK。
- retryable error NAK；最终非重试错误先持久化 FAILED 再 ACK。
- 已终态、已取消、同 delivery sequence 不可认领时只 ACK，不执行 pipeline。

### 测试

- TXT locator/ordinal/normalize/chunk boundary golden。
- duplicate delivery 不重复 parser/embedder，attempt 不重复计数。
- `after_index_write_before_complete` failpoint 后 redelivery 幂等收敛。
- `after_mark_succeeded_before_ack` 后 redelivery 只 ACK。
- chunk physical ID 包含 document/version/chunk。

### 验证与提交

```powershell
uv run pytest tests/unit/ingestion
uv run pytest -m resilience tests/resilience/test_redelivery_idempotency.py
```

提交：`feat(ingestion): 跑通 TXT Worker 与索引 Pipeline`

## 6. B5：Dense Retrieve 与 gRPC 纵向闭环

### 文件

```text
src/rag_mvp/application/retrieval_service.py
src/rag_mvp/retrieval/context_builder.py
src/rag_mvp/retrieval/provenance.py
src/rag_mvp/rpc/rag_service.py
src/rag_mvp/rpc/interceptors.py
src/rag_mvp/bootstrap/container.py
tests/unit/application/test_retrieval_service.py
tests/unit/retrieval/test_context_builder.py
tests/unit/retrieval/test_provenance.py
tests/contract/test_grpc_application_contract.py
```

### 行为

- 开放 CreateDataset、SubmitDocument、GetJob、Retrieve；其余 RPC 保持 FEATURE_NOT_AVAILABLE。
- RPC 只做 protobuf/application DTO 转换和错误映射。
- RetrievalService embed query → dense candidates → Repository active-version/删除复核 → stable top-k → ContextPlan/Evidence。
- Evidence 返回 chunk/document ID、content_with_weight、source_name、locator、dense score 和 index_version；不生成 Prompt/答案/[n]。

### 测试与提交

```powershell
uv run pytest tests/unit/application/test_retrieval_service.py tests/unit/retrieval
uv run pytest tests/contract/test_grpc_application_contract.py
```

提交：`feat(retrieval): 开放 Dense Retrieve 与 evidence 契约`

## 7. B6：Mock Functional、可观测性与全门禁

### 文件

```text
src/rag_mvp/bootstrap/container.py
tests/fakes/container.py
tests/functional/test_mock_upload_ingest_retrieve.py
tests/functional/test_mock_dedup_and_redelivery.py
tests/unit/test_observability.py
.githooks/pre-commit
.github/workflows/quality.yml
docs/README.md
docs/PLAN.md
docs/plans/milestone-b-internal-alpha.md
```

Functional harness 以 in-process gRPC channel 调用：CreateDataset → 流式 SubmitDocument → Finalizer tick → Relay tick → Worker tick → GetJob → Retrieve。测试必须断言：

- TXT evidence 正确、locator 可追溯。
- 相同 idempotency key 与相同 fingerprint 不产生第二份索引。
- Relay/Worker 重复投递最终只有一个可见版本。
- 日志上下文包含 request/job/document/dataset/stage/duration/index version/error code。
- Retry/Cancel/Delete 在 B 仍稳定返回 FEATURE_NOT_AVAILABLE。

### 完整本地门禁

```powershell
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run mypy src scripts
uv run python scripts/check_generated.py
uv run pytest tests/unit tests/contract tests/functional
uv run pytest -m resilience tests/resilience
```

提交：`test(e2e): 验证 Mock 异步摄取检索闭环`

## 8. 延期的真实基础设施验收

以下文件可先建立测试契约/占位，但没有真实服务时不声称运行通过：

```text
tests/integration/test_mysql_repository.py
tests/integration/test_elasticsearch_adapter.py
tests/integration/test_nats_jetstream_adapter.py
tests/e2e/test_upload_ingest_retrieve.py
```

MySQL schema、concrete MySQL/ES/NATS adapters、Compose E2E 和 Docker KILL 不属于本计划的 Mock Functional 完成声明。它们仍是 B 内部 Alpha 与最终 D 发布基线的阻塞项，不得用 Fake 结果替代。

## 9. Mock Functional 验收清单

- [x] B1 领域模型、状态机、digest/ID 单测通过并提交。
- [x] B2 ports 与 Fake contract 全部通过并提交。
- [x] B3 staging/fingerprint/finalizer/relay 主线通过并提交。
- [x] B4 TXT Worker/pipeline/redelivery 测试通过并提交。
- [x] B5 Dense Retrieve 与四个已开放 RPC 契约通过并提交。
- [x] B6 in-process gRPC Mock Functional 闭环与全门禁通过并提交。
- [x] 所有 Fake 仅存在于 `tests/fakes/`，生产 bootstrap 无测试导入。
- [x] 未增加 HTTP、SQLite、Qdrant、数据库队列、Agent、Chat 或 SSE。
- [x] 真实 integration/E2E 未运行项在交接和 PLAN 中明确保留。
