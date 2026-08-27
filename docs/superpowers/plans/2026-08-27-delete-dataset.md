# DeleteDataset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增不可恢复的 `DeleteDataset` gRPC，立即隔离 Dataset，并最终物理清空其 MySQL、Elasticsearch 和对象存储数据；真实 eval 在 finally 中等待自身数据被清理。

**Architecture:** Dataset 先以 generation fence 从 `ACTIVE` 进入 `DELETING`，同一事务取消文档级未完成工作并创建 dataset 作用域的 `DELETE_DATASET/CLEANUP_DATASET` Outbox 工作。Worker 对 ES 和对象执行幂等清理；成功后 MetadataRepository 在单一事务内删除完整 Dataset 聚合，因此 Job 消失（`JOB_NOT_FOUND`）是最终 purge 成功状态。

**Tech Stack:** Python 3.12、Protocol Buffers/gRPC、SQLAlchemy/Alembic/MySQL 8、Elasticsearch 8、NATS JetStream、pytest、Docker Compose

**Spec:** `docs/superpowers/specs/2026-08-27-delete-dataset-design.md`；同时遵守 `docs/SPEC.md`

## Global Constraints

- Python gRPC 是唯一公共接口；不得添加 HTTP adapter 或让 dev CLI 直连 application/adapters。
- 所有待投递 Task 与 OutboxEvent 必须在同一个 MySQL 事务创建；NATS 消息只含 `task_id`。
- 删除先在 MySQL 立即隔离，ES/对象删除必须由可重试 Worker 异步完成。
- `DeleteDataset` 不可恢复，不保留墓碑、审计记录或已完成 Job；不得用 `docker compose down -v` 作为实现。
- `.proto` 修改后必须更新 Python/Go 生成物及契约测试。
- 修改 Task/Outbox/Worker/删除语义必须覆盖重投、并发 ingest、Finalizer 竞态与 generation fence；同步更新 `tests/TEST.md`。
- Earthfile/Makefile 不在本计划变更范围；真实验收通过 `make docker-test SUITE=eval`，日常离线门禁为 `make ci`。
- 每个任务先写失败测试，再做最小实现；完成独立任务后只暂存本任务文件并用 Conventional Commit 提交，绝不 push。

---

## File Structure

| 文件 | 操作 | 职责 |
|---|---|---|
| `proto/rag/v1/rag_service.proto` | Modify | 声明 DeleteDataset、dataset JobType、删除结果和 Job 的 dataset_id |
| `src/rag_mvp/domain/enums.py`、`models.py` | Modify | Dataset 生命周期、dataset 作用域 Job/Task 纯模型 |
| `src/rag_mvp/ports/metadata.py`、`search_engine.py` | Modify | dataset cleanup 所需的 repository/search port 契约 |
| `src/rag_mvp/adapters/metadata/tables.py`、`mysql.py`、`migrations/versions/0002_delete_dataset.py` | Modify/Create | schema migration、原子删除受理及最终聚合 purge |
| `src/rag_mvp/application/document_service.py`、`cleanup_service.py`、`retrieval_service.py` | Modify | 命令编排、可见性校验、外部 cleanup |
| `src/rag_mvp/ingestion/worker.py`、`adapters/search_engine/elasticsearch.py` | Modify | dataset task 分派和 ES delete-by-query |
| `src/rag_mvp/rpc/rag_service.py`、`dev/cli.py`、`src/rag_mvp/rpc/generated/*` | Modify/Generate | gRPC DTO 映射、生成 client 与 CLI 命令 |
| `tests/fakes/metadata.py`、`tests/fakes/search_engine.py` | Modify | 与 Port 相同的确定性 dataset 删除语义 |
| `tests/unit/**`、`tests/contract/**`、`tests/integration/**`、`tests/resilience/**` | Modify/Create | schema、RPC、清理、竞态和重投测试 |
| `tests/e2e/conftest.py`、`tests/eval/test_real_*.py` | Modify | generated gRPC teardown helper 与 finally purge |
| `tests/TEST.md`、`docs/SPEC.md` | Modify | 测试登记和已实施语义同步 |

### Task 1: 领域、protobuf 与可替换 Port 契约

**Files:**
- Modify: `proto/rag/v1/rag_service.proto`, `src/rag_mvp/domain/enums.py`, `src/rag_mvp/domain/models.py`, `src/rag_mvp/application/dto.py`, `src/rag_mvp/ports/metadata.py`, `src/rag_mvp/ports/search_engine.py`
- Modify: `tests/unit/domain/test_models.py`, `tests/unit/test_generated_comparison.py`, `tests/contract/test_proto_contract.py`, `tests/contract/test_grpc_application_contract.py`, `tests/TEST.md`

**Interfaces:**
- Produces `DatasetStatus.ACTIVE/DELETING`, `JobType.DELETE_DATASET`, `TaskType.CLEANUP_DATASET`.
- Produces `DeleteDatasetCommand(request_id, idempotency_key, dataset_id, now)` and `DeleteDatasetResult(dataset_id, job_id, reused)`.
- Produces `MetadataRepository.delete_dataset()`, `dataset_cleanup_snapshot()` and `finalize_dataset_cleanup()`; `SearchEngine.delete_dataset(dataset_id)`.

- [x] Write failing tests asserting `Dataset(status=ACTIVE, lifecycle_generation=0)`, the new proto RPC/messages/enums, and that a dataset Job has `dataset_id` plus no `document_id`.
- [x] Run `uv run pytest tests/unit/domain/test_models.py tests/contract/test_proto_contract.py -q`; expect failures for missing symbols/RPC.
- [x] Add the exact enums, domain fields, DTOs and Protocol signatures. Make `Job.document_id: str | None`, require `dataset_id`, and reject document jobs without a document or dataset jobs with one.

```python
class TaskType(StrEnum):
    CLEANUP_DATASET = "CLEANUP_DATASET"

@dataclass(frozen=True, slots=True)
class DeleteDatasetCommand:
    request_id: str
    idempotency_key: str
    dataset_id: str
    now: datetime
```
- [x] Regenerate protobuf using the repository’s existing `make proto` entrypoint; do not hand-edit generated files. Extend generated-comparison and gRPC contract tests to require `DeleteDataset`, field numbers 1/2 and `JobResult.dataset_id = 11`.
- [x] Run `make proto` and `uv run pytest tests/unit/domain/test_models.py tests/unit/test_generated_comparison.py tests/contract/test_proto_contract.py tests/contract/test_grpc_application_contract.py -q`; expect PASS.
- [x] Update `tests/TEST.md`, inspect `git status --short`, then commit only this task: `feat(rpc): 定义数据集删除契约`.

### Task 2: MySQL 生命周期、迁移与立即不可见性

**Files:**
- Modify: `src/rag_mvp/adapters/metadata/tables.py`, `src/rag_mvp/adapters/metadata/mysql.py`, `src/rag_mvp/application/document_service.py`, `src/rag_mvp/application/retrieval_service.py`
- Create: `migrations/versions/0002_delete_dataset.py`
- Modify: `tests/unit/adapters/test_mysql_schema.py`, `tests/integration/test_mysql_migrations.py`, `tests/integration/test_mysql_lifecycle.py`, `tests/integration/test_mysql_concurrency.py`, `tests/fakes/metadata.py`

**Interfaces:**
- Consumes Task 1 contracts.
- Produces `delete_dataset(DeleteDatasetRequest) -> DeleteDatasetResult`; a `DELETING` Dataset cannot be submitted, retrieved or made visible.

- [x] Write integration failures for: deletion atomically creates one READY dataset cleanup outbox; immediately hides all versions; cancels active document tasks/outbox; same key reuses; different key returns `DATASET_DELETION_IN_PROGRESS`.
- [x] Run `uv run pytest -m integration tests/integration/test_mysql_lifecycle.py tests/integration/test_mysql_concurrency.py -q`; expect missing migration/behavior failures.
- [x] Add migration fields (`datasets.status`, `datasets.lifecycle_generation`, `jobs.dataset_id`, nullable `jobs.document_id`, `idempotency_records.dataset_id`), backfill existing jobs and idempotency rows from their existing document/dataset result, add constraints/indexes, and provide downgrade. Update ORM conversion functions and Fake repository identically.

```python
await session.execute(
    update(DatasetTable)
    .where(DatasetTable.id == request.dataset_id, DatasetTable.status == DatasetStatus.ACTIVE)
    .values(status=DatasetStatus.DELETING, lifecycle_generation=DatasetTable.lifecycle_generation + 1)
)
```
- [x] In one locked MySQL transaction implement the four ordered operations in the design: fence Dataset, fence documents/release fingerprints, cancel unfinished work, create dataset Job/Task/Outbox + idempotency record. Make submit/retrieve/visible-version checks reject non-ACTIVE Dataset.
- [x] Run `uv run pytest tests/unit/adapters/test_mysql_schema.py -q` and `uv run pytest -m integration tests/integration/test_mysql_migrations.py tests/integration/test_mysql_lifecycle.py tests/integration/test_mysql_concurrency.py -q`; expect PASS with Docker MySQL available.（本次：25 个相关 unit PASS；12 个真实 MySQL integration PASS。）
- [x] Update `tests/TEST.md`, inspect status, commit: `feat(mysql): 隔离待删除知识库`.

> 执行顺序更新（2026-08-27）：Task 1 的生成 gRPC 注册代码会立即访问 `RagService.DeleteDataset`，因此 Task 4 必须在 Task 2 后、Task 3 前执行，避免中间分支无法启动 gRPC server。任务编号和各自提交边界保持不变。

### Task 3: Worker 的 dataset 外部清理与最终物理 purge

**Files:**
- Modify: `src/rag_mvp/application/cleanup_service.py`, `src/rag_mvp/ingestion/worker.py`, `src/rag_mvp/adapters/search_engine/elasticsearch.py`, `tests/fakes/search_engine.py`, `tests/fakes/metadata.py`
- Modify: `tests/unit/ingestion/test_worker.py`, `tests/contract/test_search_engine_contract.py`, `tests/integration/test_elasticsearch_adapter.py`, `tests/integration/test_mysql_lifecycle.py`
- Create: `tests/unit/application/test_cleanup_service.py`

**Interfaces:**
- Consumes `TaskType.CLEANUP_DATASET`, `SearchEngine.delete_dataset(dataset_id) -> None`, snapshot object keys and `finalize_dataset_cleanup(task_id, now) -> bool`.
- Produces idempotent ES delete-by-query, object deletion and child-to-parent MySQL purge.

- [ ] Write failing tests that cleanup calls ES dataset delete before every snapshot object delete, retries a storage/ES failure without purging rows, and on success removes Outbox/Task/Job/fingerprint/document/Dataset rows. Assert a late delivery with no task is ACKed.
- [ ] Run `uv run pytest tests/unit/ingestion/test_worker.py tests/unit/application/test_cleanup_service.py tests/contract/test_search_engine_contract.py -q`; expect unsupported task/port failures.
- [ ] Implement `delete_dataset` in Elasticsearch using a dataset-id filtered `delete_by_query` with conflict-tolerant completion. Extend CleanupService dispatch so only `CLEANUP_DATASET` uses the snapshot/finalizer path; preserve existing document cleanup behavior.

```python
if claim.task.type is TaskType.CLEANUP_DATASET:
    await self._search.delete_dataset(claim.dataset.id)
    for object_key in await self._metadata.dataset_cleanup_object_keys(task_id):
        await self._storage.delete(object_key)
    completed = await self._metadata.finalize_dataset_cleanup(task_id, now)
```
- [ ] Implement final MySQL purge in one transaction: lock cleanup aggregate and dataset generation, clear job retry self-references, delete children in foreign-key order, then delete Dataset. Do not write a SUCCEEDED row that would survive purge.
- [ ] Run `uv run pytest tests/unit/ingestion/test_worker.py tests/unit/application/test_cleanup_service.py tests/contract/test_search_engine_contract.py -q` and `uv run pytest -m integration tests/integration/test_mysql_lifecycle.py tests/integration/test_elasticsearch_adapter.py -q`; expect PASS.
- [ ] Update `tests/TEST.md`, inspect status, commit: `feat(ingestion): 清理并物理删除知识库数据`.

### Task 4: gRPC、CLI 与 lifecycle 端到端语义

**Files:**
- Modify: `src/rag_mvp/rpc/rag_service.py`, `dev/cli.py`, `tests/unit/test_dev_cli.py`, `tests/unit/test_process_lifecycle.py`, `tests/functional/test_mock_upload_ingest_retrieve.py`
- Modify: `tests/contract/test_proto_contract.py`, `tests/contract/test_grpc_application_contract.py`, `tests/TEST.md`

**Interfaces:**
- Consumes `DocumentService.delete_dataset(DeleteDatasetCommand)`.
- Produces `RagService.DeleteDataset` and `dev/cli.py delete-dataset --request-id --idempotency-key --dataset-id`.

- [ ] Write failing RPC tests for success mapping, empty idempotency rejection, reuse, `DATASET_DELETION_IN_PROGRESS`, and `DATASET_NOT_FOUND`; write CLI parser test requiring all three mutation identifiers.
- [ ] Run `uv run pytest tests/unit/test_dev_cli.py tests/unit/test_process_lifecycle.py tests/contract/test_proto_contract.py tests/contract/test_grpc_application_contract.py -q`; expect method/command failures.
- [ ] Add RPC conversion only; do not import adapters into `rpc/rag_service.py`. Add generated-client CLI command and a functional test that a deleted Dataset’s retrieval is rejected before worker completion.

```python
result = await self._documents.delete_dataset(
    DeleteDatasetCommand(request.context.request_id, request.context.idempotency_key,
                         request.dataset_id, self._now())
)
return rag_service_pb2.DeleteDatasetResponse(
    result=rag_service_pb2.DeleteDatasetResult(dataset_id=result.dataset_id, job_id=result.job_id)
)
```
- [ ] Run the preceding unit/contract tests plus `uv run pytest tests/functional/test_mock_upload_ingest_retrieve.py -q`; expect PASS.
- [ ] Update `tests/TEST.md`, inspect status, commit: `feat(rpc): 暴露知识库删除接口`.

### Task 5: 删除并发与真实 eval 的一次性 teardown

**Files:**
- Modify: `tests/resilience/test_generation_fences.py`, `tests/resilience/test_cancel_races.py`, `tests/e2e/conftest.py`, `tests/eval/test_real_retrieval_quality.py`, `tests/eval/test_real_computer_architecture_pdf_quality.py`, `tests/TEST.md`
- Modify: `docs/SPEC.md`, `docs/superpowers/specs/2026-08-27-delete-dataset-design.md`

**Interfaces:**
- Produces `delete_dataset(stub, dataset_id) -> str` and `wait_for_dataset_purged(stub, job_id, deadline_seconds=240) -> None`.

- [ ] Write failing resilience tests that delete races with an in-flight ingest and Finalizer; assert no generation-mismatched worker can activate a document, and published stale delivery is ACK-only.
- [ ] Write failing eval-helper tests: terminal `FAILED/CANCELLED` raises; observed `JOB_NOT_FOUND` after deletion job is accepted as purge success; timeout includes the job id.
- [ ] Run `uv run pytest -m resilience tests/resilience/test_generation_fences.py tests/resilience/test_cancel_races.py -q` and `uv run pytest tests/eval/test_real_computer_architecture_pdf_quality.py -q`; expect failures.
- [ ] Implement gRPC-only helpers. Refactor both real evals to wrap create/upload/evaluate in `try/finally`; write logs before metric assertion, then invoke delete/wait in finally. Preserve the original test exception if cleanup succeeds; chain cleanup error if it fails.

```python
dataset_id = await create_dataset(...)
try:
    document_id, job_id = await submit_document(...)
    await wait_for_job(stub, job_id, deadline_seconds=600)
    # evaluate and write the JSON log before asserting thresholds
finally:
    deletion_job_id = await delete_dataset(stub, dataset_id)
    await wait_for_dataset_purged(stub, deletion_job_id)
```
- [ ] Run `uv run pytest -m resilience tests/resilience/test_generation_fences.py tests/resilience/test_cancel_races.py -q` and `uv run pytest -m "eval and not e2e" tests/eval -q`; expect PASS.
- [ ] Update specs/tests registry, inspect status, commit: `test(eval): 清理真实评测知识库数据`.

### Task 6: 完整验证与真实 Docker 验收

**Files:**
- Modify only if verification exposes a specification mismatch: `docs/SPEC.md`, `docs/superpowers/specs/2026-08-27-delete-dataset-design.md`, `docs/superpowers/plans/2026-08-27-delete-dataset.md`, `tests/TEST.md`

- [ ] Run `make ci`; expect all offline/unit/functional/contract gates PASS.
- [ ] Run `make docker-test SUITE=integration`; expect MySQL migration, lifecycle, ES and NATS paths PASS.
- [ ] Run `make docker-test SUITE=resilience`; expect deletion race/redelivery scenarios PASS.
- [ ] Run `make docker-test SUITE=eval`; run it a second time and confirm each run logs normally with no persistent eval Dataset records/chunks/objects from the prior run.
- [ ] Run `git diff --check`, `git status --short` and targeted `docker compose` read-only queries to confirm no eval IDs remain. Record only actual command outputs in plan checkboxes; do not commit unrelated user changes.
