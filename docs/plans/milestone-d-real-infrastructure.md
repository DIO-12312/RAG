# Milestone D 真实基础设施实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task in the current session. Steps use checkbox (`- [ ]`) syntax for tracking. Each numbered task is one independently reviewed and committed module; never combine completed tasks into one commit.

**Goal:** 在 Docker Compose 中用真实 MySQL、Elasticsearch、NATS JetStream 和 OpenAI-compatible Embedding API 跑通可靠、可恢复、可检索的 Python RAG，并完成真实 integration/E2E/resilience/eval 发布门禁。

**Architecture:** 保持现有 domain/application/ports 边界不变，由 `bootstrap/container.py` 按 Server、Worker、Outbox 角色装配 concrete adapters。MySQL 是状态事实源，Outbox 只发布 READY `task_id`，JetStream 至少一次投递，Worker 调用真实模型并幂等写入版本化 ES；Docker 测试只能从 gRPC 入口驱动业务。

**Tech Stack:** Python 3.12+、uv、gRPC、SQLAlchemy 2 Async、asyncmy、Alembic、httpx、Elasticsearch 8 async client、nats-py、MySQL 8/InnoDB、Elasticsearch 8.19、NATS JetStream 2.11、pytest、Docker Compose。

**Spec:** `docs/SPEC.md`、`docs/PLAN.md`、`docs/superpowers/specs/2026-08-24-real-infrastructure-rag-design.md`

## Global Constraints

- Python 只提供版本化 gRPC；不得新增 HTTP/FastAPI 业务 adapter。
- MySQL、Elasticsearch、NATS JetStream 分别是唯一元数据、检索和异步任务基础设施。
- NATS 消息只传 `task_id`；Task 与 OutboxEvent 必须同一 MySQL 事务创建。
- Worker 是唯一 consume/ACK/NAK 的位置；application service 不直接操作 NATS delivery。
- `chunk_id` 与 ES `_id` 必须保持 SPEC 规定的 RAGFlow xxHash64 和 `{document_id}:{index_version}:{chunk_id}`。
- MVP 一个运行实例只允许一个 Embedding model/dimension；Dataset 声明必须匹配运行配置。
- Docker E2E、model integration 和 real eval 禁止 Fake ports；被选择运行时缺少 Secret 必须失败。
- `.env`、API Key、真实上传数据、Docker logs 和 `data/` 不得提交；测试输出不得泄漏密钥。
- 每个任务严格 RED → GREEN → REFACTOR；新增/改名测试时同提交更新 `tests/TEST.md`。
- 每个任务完成后检查 `git status`，只暂存该任务拥有的文件并立即 commit；不得 push。

---

## Task 1：修复跨平台 protobuf 生成检查

**Files:**
- Create: `.gitattributes`
- Modify: `scripts/check_generated.py`
- Create: `tests/unit/test_generated_comparison.py`
- Modify: `tests/TEST.md`

**Interfaces:**
- Produces: `normalized_generated_text(path: Path) -> str`
- Produces: `generated_files_match(checked_in: Path, regenerated: Path) -> bool`
- Preserves: `scripts/check_generated.py` CLI exit code and existing `GENERATED_FILES`

- [x] **Step 1: Write the failing line-ending regression tests**

```python
def test_generated_comparison_ignores_only_line_endings(tmp_path: Path) -> None:
    checked = tmp_path / "checked.py"
    regenerated = tmp_path / "regenerated.py"
    checked.write_bytes(b"alpha\r\nbeta\r\n")
    regenerated.write_bytes(b"alpha\nbeta\n")
    assert generated_files_match(checked, regenerated)


def test_generated_comparison_rejects_content_changes(tmp_path: Path) -> None:
    checked = tmp_path / "checked.py"
    regenerated = tmp_path / "regenerated.py"
    checked.write_text("alpha\n", encoding="utf-8")
    regenerated.write_text("omega\n", encoding="utf-8")
    assert not generated_files_match(checked, regenerated)
```

- [x] **Step 2: Verify RED**

Run: `uv run pytest tests/unit/test_generated_comparison.py -q`

Expected: collection/import fails because `generated_files_match` does not exist.

- [x] **Step 3: Implement normalized comparison and LF attributes**

```python
def normalized_generated_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")


def generated_files_match(checked_in: Path, regenerated: Path) -> bool:
    return normalized_generated_text(checked_in) == normalized_generated_text(regenerated)
```

`.gitattributes` must contain:

```gitattributes
*.proto text eol=lf
scripts/generate_proto.py text eol=lf
scripts/check_generated.py text eol=lf
src/rag_mvp/rpc/generated/* text eol=lf
```

- [x] **Step 4: Verify GREEN and no weakened content check**

Run:

```powershell
uv run pytest tests/unit/test_generated_comparison.py tests/contract/test_generated_code.py -q
uv run python scripts/check_generated.py
uv run ruff check scripts tests/unit/test_generated_comparison.py
uv run mypy scripts
```

Expected: all commands exit 0 on the current CRLF checkout.

- [x] **Step 5: Update `tests/TEST.md` and commit**

```powershell
git add .gitattributes scripts/check_generated.py tests/unit/test_generated_comparison.py tests/TEST.md docs/plans/milestone-d-real-infrastructure.md
git commit -m "fix(build): 修复跨平台 protobuf 同步检查"
```

---

## Task 2：锁定真实基础设施运行依赖与 pytest markers

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `tests/unit/test_config.py`
- Modify: `tests/TEST.md`

**Interfaces:**
- Produces installed modules: `sqlalchemy`, `asyncmy`, `alembic`, `httpx`, `elasticsearch`, `nats`
- Produces pytest markers: `model_integration`, `docker_resilience`；保留现有 `e2e`

- [ ] **Step 1: Add a dependency/marker smoke test that fails before lock update**

```python
@pytest.mark.parametrize(
    "module_name",
    ["sqlalchemy", "asyncmy", "alembic", "httpx", "elasticsearch", "nats"],
)
def test_runtime_adapter_dependencies_are_importable(module_name: str) -> None:
    assert importlib.util.find_spec(module_name) is not None
```

Extend the marker assertion to require all three new marker names.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/unit/test_config.py -q`

Expected: at least SQLAlchemy/asyncmy/Alembic/ES/NATS imports and marker assertions fail.

- [ ] **Step 3: Add bounded dependencies**

Add production dependencies:

```toml
"SQLAlchemy[asyncio]>=2.0.43,<3.0.0",
"asyncmy>=0.2.10,<1.0.0",
"alembic>=1.16.5,<2.0.0",
"httpx>=0.28.1,<1.0.0",
"elasticsearch[async]>=8.19.0,<9.0.0",
"nats-py>=2.11.0,<3.0.0",
```

Register:

```toml
"model_integration: calls the configured real embedding provider",
"docker_resilience: controls Docker processes to validate crash recovery",
```

Regenerate lock: `uv lock` then `uv sync --frozen --group dev`.

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
uv run pytest tests/unit/test_config.py -q
uv run ruff check tests/unit/test_config.py
uv run mypy src scripts
```

- [ ] **Step 5: Update test index and commit**

```powershell
git add pyproject.toml uv.lock tests/unit/test_config.py tests/TEST.md docs/plans/milestone-d-real-infrastructure.md
git commit -m "build(deps): 增加真实 RAG 基础设施依赖"
```

---

## Task 3：建立生产 Settings、默认租户和模型配置栅栏

**Files:**
- Modify: `.env.example`
- Modify: `src/rag_mvp/config.py`
- Modify: `src/rag_mvp/domain/models.py`
- Modify: `src/rag_mvp/application/document_service.py`
- Modify: `tests/unit/test_config.py`
- Modify: `tests/unit/domain/test_models.py`
- Modify: `tests/unit/application/test_document_service.py`
- Modify: `tests/fakes/container.py`
- Modify: `tests/TEST.md`

**Interfaces:**
- Produces: `EmbeddingProfile(endpoint, model, api_key, dimension, batch_size, timeout_seconds, max_retries)`
- Produces optional raw fields: `Settings.embedding_model_url/name/api_key/dimension`
- Produces: `Settings.require_embedding_profile() -> EmbeddingProfile`
- Produces: `Settings.embedding_batch_size`, `embedding_timeout_seconds`, `embedding_max_retries`
- Produces: `Settings.default_tenant_id`, `elasticsearch_index`, NATS ack/delivery/poll settings
- Changes: `Dataset(..., tenant_id: str)`
- Changes: `DocumentService(..., default_tenant_id: str, embedding_model: str, embedding_dimension: int)`

- [ ] **Step 1: Write failing config and Dataset tests**

Tests must assert:

```python
settings = Settings(
    _env_file=None,
    embedding_model_url="https://model.example/v1",
    embedding_model_name="embedding-model",
    embedding_model_api_key="secret",
    embedding_model_dimension=1024,
)
assert settings.require_embedding_profile().endpoint == "https://model.example/v1/embeddings"
assert Dataset(..., tenant_id="default_tenant").tenant_id == "default_tenant"
```

An otherwise valid `Settings(_env_file=None)` without model variables must remain constructible for Outbox and pure unit tests, while `settings.require_embedding_profile()` must fail with a stable configuration error. `DocumentService.create_dataset` must reject a requested model/dimension mismatch with `EMBEDDING_CONFIG_MISMATCH`.

- [ ] **Step 2: Verify RED**

Run:

```powershell
uv run pytest tests/unit/test_config.py tests/unit/domain/test_models.py tests/unit/application/test_document_service.py -q
```

Expected: missing Settings fields, missing `tenant_id`, and old DocumentService constructor fail.

- [ ] **Step 3: Implement validated settings and domain fields**

Exact production defaults/constraints:

```python
default_tenant_id: str = "default_tenant"
embedding_model_url: str | None = Field(default=None, validation_alias="EMBEDDING_MODEL_URL")
embedding_model_name: str | None = Field(default=None, validation_alias="EMBEDDING_MODEL_NAME")
embedding_model_api_key: SecretStr | None = Field(default=None, validation_alias="EMBEDDING_MODEL_API_KEY")
embedding_model_dimension: int | None = Field(default=None, gt=0, validation_alias="EMBEDDING_MODEL_DIMENSION")
embedding_batch_size: int = Field(default=32, ge=1, le=256)
embedding_timeout_seconds: float = Field(default=30.0, gt=0)
embedding_max_retries: int = Field(default=3, ge=0, le=10)
elasticsearch_index: str = "rag-chunks-v1"
nats_ack_wait_seconds: float = Field(default=60.0, gt=0)
nats_max_deliver: int = Field(default=3, ge=1)
```

Set `populate_by_name=True` in `SettingsConfigDict` so explicit test construction and environment aliases both work. `require_embedding_profile()` rejects partial/missing provider config and is called only by Server/Worker builders; Outbox never receives the API Key. `SecretStr` must never be interpolated by `repr(settings)` or structured logs. Add `EMBEDDING_MODEL_DIMENSION=` and non-secret tuning keys to `.env.example`; do not edit or stage `.env`.

- [ ] **Step 4: Verify all Fake tests remain deterministic**

Run:

```powershell
uv run pytest tests/unit tests/contract tests/functional -q
uv run ruff check src tests
uv run mypy src
```

- [ ] **Step 5: Commit only configuration/domain changes**

```powershell
git add .env.example src/rag_mvp/config.py src/rag_mvp/domain/models.py src/rag_mvp/application/document_service.py tests/unit/test_config.py tests/unit/domain/test_models.py tests/unit/application/test_document_service.py tests/fakes/container.py tests/TEST.md docs/plans/milestone-d-real-infrastructure.md
git commit -m "feat(config): 固定租户与 embedding 运行配置"
```

---

## Task 4：建立 MySQL schema、Alembic migration 与连接工厂

**Files:**
- Create: `alembic.ini`
- Create: `migrations/env.py`
- Create: `migrations/script.py.mako`
- Create: `migrations/versions/0001_core_schema.py`
- Create: `src/rag_mvp/adapters/metadata/tables.py`
- Create: `src/rag_mvp/adapters/metadata/database.py`
- Create: `src/rag_mvp/adapters/metadata/migrate.py`
- Create: `tests/unit/adapters/test_mysql_schema.py`
- Create: `tests/integration/conftest.py`
- Create: `tests/integration/test_mysql_migrations.py`
- Modify: `pyproject.toml`（增加 `rag-migrate` console script）
- Modify: `tests/TEST.md`

**Interfaces:**
- Produces: `Base`, SQLAlchemy table classes for all design entities
- Produces: `create_mysql_engine(dsn: str) -> AsyncEngine`
- Produces: `create_session_factory(engine) -> async_sessionmaker[AsyncSession]`
- Produces CLI: `rag-migrate`

- [ ] **Step 1: Write failing schema metadata tests**

Assert exact tables and constraints:

```python
assert EXPECTED_TABLES <= set(Base.metadata.tables)
assert unique_columns("ingestion_fingerprints") == {
    ("dataset_id", "file_sha256", "config_digest")
}
assert unique_columns("index_builds") == {("document_id", "index_version")}
assert unique_columns("idempotency_records") == {
    ("operation_type", "idempotency_key")
}
```

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/unit/adapters/test_mysql_schema.py -q`

Expected: import fails because `tables.py` does not exist.

- [ ] **Step 3: Implement final schema and migration**

Use string enum columns sized for current enum values, UTC `DATETIME(6)`, JSON for errors/locator/metadata, binary-safe 64-character digest columns, foreign keys and the design constraints. `chunk_manifests` stores no vector.

- [ ] **Step 4: Start MySQL and verify real migration twice**

Run:

```powershell
docker compose up -d mysql
uv run rag-migrate upgrade head
uv run rag-migrate upgrade head
uv run pytest -m integration tests/integration/test_mysql_migrations.py -q
```

Expected: both upgrades exit 0; all expected InnoDB tables and unique constraints exist.

- [ ] **Step 5: Run static and import-boundary checks**

```powershell
uv run ruff check src/rag_mvp/adapters/metadata migrations tests/unit/adapters tests/integration
uv run mypy src migrations
uv run pytest tests/unit/test_import_boundaries.py -q
```

- [ ] **Step 6: Update test index and commit**

```powershell
git add alembic.ini migrations/env.py migrations/script.py.mako migrations/versions/0001_core_schema.py pyproject.toml src/rag_mvp/adapters/metadata/tables.py src/rag_mvp/adapters/metadata/database.py src/rag_mvp/adapters/metadata/migrate.py tests/unit/adapters/test_mysql_schema.py tests/integration/conftest.py tests/integration/test_mysql_migrations.py tests/TEST.md docs/plans/milestone-d-real-infrastructure.md
git commit -m "feat(mysql): 建立核心 schema 与迁移"
```

---

## Task 5：实现 MySQL Dataset、幂等上传和 Fingerprint 事务

**Files:**
- Create: `src/rag_mvp/adapters/metadata/mysql.py`
- Create: `src/rag_mvp/adapters/metadata/mappers.py`
- Create: `tests/integration/test_mysql_submission.py`
- Modify: `tests/contract/test_metadata_repository_contract.py`
- Modify: `tests/TEST.md`

**Interfaces:**
- Produces: `MySQLMetadataRepository(session_factory, default_tenant_id)`
- Implements: `create_dataset`, `get_dataset`, `submit_ingestion`, `get_job`, `get_task`, `get_task_for_job`, `get_document`

- [ ] **Step 1: Parameterize repository contract and write real concurrency tests**

The real suite must assert:

```python
first, second = await asyncio.gather(
    repository.submit_ingestion(command_a),
    repository.submit_ingestion(command_b),
)
assert first.document_id == second.document_id
assert first.job_id == second.job_id
assert await count_rows("tasks") == 1
assert await count_rows("outbox_events") == 1
```

Also inject an exception before commit and assert zero partial Document/Job/Task/Outbox rows.

- [ ] **Step 2: Verify RED against MySQL fixture**

Run: `uv run pytest -m integration tests/integration/test_mysql_submission.py -q`

Expected: `MySQLMetadataRepository` import/implementation failure.

- [ ] **Step 3: Implement transaction and mapper methods**

`submit_ingestion` must use one `async with session.begin()` and lock/create fingerprint before Document/Job/Task/Outbox/IndexBuild. Duplicate idempotency returns the recorded result; fingerprint loser returns canonical Job and `staging_referenced=False`.

- [ ] **Step 4: Verify GREEN and Task/Outbox atomicity**

```powershell
uv run pytest -m integration tests/integration/test_mysql_submission.py -q
uv run pytest tests/contract/test_metadata_repository_contract.py -q
uv run ruff check src/rag_mvp/adapters/metadata tests/integration/test_mysql_submission.py
uv run mypy src
```

- [ ] **Step 5: Commit**

```powershell
git add src/rag_mvp/adapters/metadata/mysql.py src/rag_mvp/adapters/metadata/mappers.py tests/integration/test_mysql_submission.py tests/contract/test_metadata_repository_contract.py tests/TEST.md docs/plans/milestone-d-real-infrastructure.md
git commit -m "feat(mysql): 实现上传去重与原子任务事务"
```

---

## Task 6：实现 MySQL Finalizer、Outbox 和 Worker 条件状态

**Files:**
- Modify: `src/rag_mvp/adapters/metadata/mysql.py`
- Create: `tests/integration/test_mysql_outbox_worker.py`
- Modify: `tests/contract/test_metadata_repository_contract.py`
- Modify: `tests/TEST.md`

**Interfaces:**
- Implements: `list_waiting_outbox`, `mark_object_ready`, `record_finalization_failure`, `waiting_staging_keys`
- Implements: `list_ready_outbox`, `mark_outbox_published`
- Implements: `claim_task`, `complete_ingestion`, `fail_task`

- [ ] **Step 1: Write failing condition-update tests**

Test these database-visible outcomes:

```python
assert not await repository.mark_object_ready(event_id, object_key, now_after_delete)
assert await repository.claim_task(task_id, 10, now) is not None
assert await repository.claim_task(task_id, 10, now) is None
assert await repository.claim_task(task_id, 11, now) is not None
assert await repository.complete_ingestion(task_id, chunks, now)
assert visible_versions == {document_id: 1}
```

Finalizer exhaustion must atomically write Job/Task FAILED, Outbox CANCELLED and Fingerprint RELEASED.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest -m integration tests/integration/test_mysql_outbox_worker.py -q`

Expected: methods are missing or return incorrect state.

- [ ] **Step 3: Implement lock/condition semantics**

Use short transactions. `claim_task` compares consumer sequence against `last_delivery_sequence`; `complete_ingestion` rechecks cancellation, Document status and generation before manifest/version activation.

- [ ] **Step 4: Verify GREEN**

```powershell
uv run pytest -m integration tests/integration/test_mysql_outbox_worker.py -q
uv run pytest tests/unit/outbox tests/unit/ingestion tests/contract/test_metadata_repository_contract.py -q
uv run mypy src
```

- [ ] **Step 5: Commit**

```powershell
git add src/rag_mvp/adapters/metadata/mysql.py tests/integration/test_mysql_outbox_worker.py tests/contract/test_metadata_repository_contract.py tests/TEST.md docs/plans/milestone-d-real-infrastructure.md
git commit -m "feat(mysql): 实现 Outbox 与 Worker 条件状态"
```

---

## Task 7：实现 MySQL Retry、Cancel、Delete、Cleanup 与可见性

**Files:**
- Modify: `src/rag_mvp/adapters/metadata/mysql.py`
- Create: `tests/integration/test_mysql_lifecycle.py`
- Create: `tests/integration/test_mysql_concurrency.py`
- Modify: `tests/contract/test_retry_job_contract.py`
- Modify: `tests/contract/test_delete_document_contract.py`
- Modify: `tests/TEST.md`

**Interfaces:**
- Implements: `retry_job`, `cancel_job`, `delete_document`, `complete_cleanup`, `visible_document_versions`

- [ ] **Step 1: Write failing real lifecycle/concurrency tests**

Required assertions:

```python
children = await asyncio.gather(*(repository.retry_job(request) for _ in range(8)))
assert len({child.job_id for child in children}) == 1

versions = await asyncio.gather(*(submit_rebuild() for _ in range(4)))
assert len({version.index_version for version in versions}) == 4

await repository.delete_document(delete_request)
assert await repository.visible_document_versions([document_id]) == {}
assert not await repository.complete_ingestion(old_task_id, chunks, now)
```

- [ ] **Step 2: Verify RED**

Run: `uv run pytest -m integration tests/integration/test_mysql_lifecycle.py tests/integration/test_mysql_concurrency.py -q`

- [ ] **Step 3: Implement row-lock and unique-constraint paths**

Retry locks original Job and reuses active child. Delete locks Document, increments generation, releases fingerprint, cancels ingest/Outbox, and atomically creates cleanup Job/Task/READY Outbox. Cleanup checks generation before success.

- [ ] **Step 4: Verify GREEN**

```powershell
uv run pytest -m integration tests/integration/test_mysql_lifecycle.py tests/integration/test_mysql_concurrency.py -q
uv run pytest tests/contract/test_retry_job_contract.py tests/contract/test_delete_document_contract.py tests/resilience -q
uv run mypy src
```

- [ ] **Step 5: Commit**

```powershell
git add src/rag_mvp/adapters/metadata/mysql.py tests/integration/test_mysql_lifecycle.py tests/integration/test_mysql_concurrency.py tests/contract/test_retry_job_contract.py tests/contract/test_delete_document_contract.py tests/TEST.md docs/plans/milestone-d-real-infrastructure.md
git commit -m "feat(mysql): 实现文档生命周期与并发栅栏"
```

---

## Task 8：实现真实 OpenAI-compatible Embedding Gateway

**Files:**
- Create: `src/rag_mvp/adapters/model/openai_compatible.py`
- Create: `tests/unit/adapters/test_openai_compatible_model.py`
- Create: `tests/integration/test_real_embedding_model.py`
- Modify: `tests/contract/test_model_gateway_contract.py`
- Modify: `tests/TEST.md`

**Interfaces:**
- Produces: `OpenAICompatibleModelGateway(client, endpoint, model, dimension, batch_size, max_retries)`
- Implements: `embed(texts: list[str]) -> list[tuple[float, ...]]`
- Implements: `rerank(...)` as retryable `RERANK_UNAVAILABLE` until a real rerank endpoint is configured
- Produces: `close() -> None`

- [ ] **Step 1: Write failing MockTransport unit tests**

Cover URL normalization, Bearer header without logging, response index reordering, batch splitting, NaN rejection, count/dimension mismatch, 401 non-retryable, 429/5xx bounded retry and timeout mapping.

```python
vectors = await gateway.embed(["a", "b", "c"])
assert vectors == [vector_for_a, vector_for_b, vector_for_c]
assert all(len(vector) == 1024 for vector in vectors)
```

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/unit/adapters/test_openai_compatible_model.py -q`

- [ ] **Step 3: Implement minimal async adapter**

Use `httpx.AsyncClient`, chunked batches and `asyncio.sleep` backoff. Never include provider response body in `DomainFailure.message`; only stable status/error classification.

- [ ] **Step 4: Verify unit GREEN**

Run: `uv run pytest tests/unit/adapters/test_openai_compatible_model.py tests/contract/test_model_gateway_contract.py -q`

- [ ] **Step 5: Run mandatory real model tests on host**

Ensure local `.env` contains the previously measured non-secret declaration `EMBEDDING_MODEL_DIMENSION=1024`, without printing the file.

Run:

```powershell
uv run pytest -m model_integration tests/integration/test_real_embedding_model.py -q
```

Expected: single, batch, finite 1024-dimension and Chinese relative-similarity tests pass; missing configuration is FAIL, not SKIP.

- [ ] **Step 6: Commit**

```powershell
git add src/rag_mvp/adapters/model/openai_compatible.py tests/unit/adapters/test_openai_compatible_model.py tests/integration/test_real_embedding_model.py tests/contract/test_model_gateway_contract.py tests/TEST.md docs/plans/milestone-d-real-infrastructure.md
git commit -m "feat(model): 接入真实 OpenAI 兼容 embedding"
```

---

## Task 9：实现 Elasticsearch Dense/BM25 SearchEngine

**Files:**
- Create: `src/rag_mvp/adapters/search_engine/elasticsearch.py`
- Create: `src/rag_mvp/adapters/search_engine/mapping.py`
- Create: `tests/unit/adapters/test_elasticsearch_mapping.py`
- Create: `tests/integration/test_elasticsearch_adapter.py`
- Modify: `tests/contract/test_search_engine_contract.py`
- Modify: `tests/TEST.md`

**Interfaces:**
- Produces: `ElasticsearchSearchEngine(client, index_name, embedding_dimension)`
- Produces: `ensure_index() -> None`, `close() -> None`
- Implements all five existing `SearchEngine` methods

- [ ] **Step 1: Write failing mapping and document-codec tests**

Assert `dense_vector.dims`, cosine similarity, keyword/text/flattened fields and round-trip `IndexedChunk → ES source → SearchCandidate` without score loss.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/unit/adapters/test_elasticsearch_mapping.py -q`

- [ ] **Step 3: Implement mapping, Bulk upsert and separate searches**

Dense request must contain KNN plus `dataset_id`/metadata filters; sparse request must use BM25 match plus the same filters. Both sort equal scores by `_id`. Adapter must not import or call RRF.

- [ ] **Step 4: Start ES and run real integration contract**

```powershell
docker compose up -d elasticsearch
uv run pytest -m integration tests/integration/test_elasticsearch_adapter.py -q
uv run pytest tests/contract/test_search_engine_contract.py -q
```

Assert idempotent upsert, Dense relevant hit, BM25 exact term, Dataset isolation, metadata filter, version delete and full document delete.

- [ ] **Step 5: Verify boundaries and commit**

```powershell
uv run ruff check src/rag_mvp/adapters/search_engine tests/unit/adapters tests/integration/test_elasticsearch_adapter.py
uv run mypy src
git add src/rag_mvp/adapters/search_engine/elasticsearch.py src/rag_mvp/adapters/search_engine/mapping.py tests/unit/adapters/test_elasticsearch_mapping.py tests/integration/test_elasticsearch_adapter.py tests/contract/test_search_engine_contract.py tests/TEST.md docs/plans/milestone-d-real-infrastructure.md
git commit -m "feat(search): 实现 ES 稠密与 BM25 检索"
```

---

## Task 10：实现 NATS JetStream TaskQueue

**Files:**
- Create: `src/rag_mvp/adapters/message_queue/nats_jetstream.py`
- Create: `tests/unit/adapters/test_nats_delivery_mapping.py`
- Create: `tests/integration/test_nats_jetstream_adapter.py`
- Modify: `tests/contract/test_task_queue_contract.py`
- Modify: `tests/TEST.md`

**Interfaces:**
- Produces async factory: `NatsJetStreamTaskQueue.connect(url, stream, subject, consumer, ack_wait_seconds, max_deliver)`
- Implements: `publish`, `consume`, `ack`, `nak`
- Produces: `close() -> None`

- [ ] **Step 1: Write failing delivery metadata mapping test**

```python
delivery = delivery_from_message(message)
assert delivery.task_id == "task-1"
assert delivery.delivery_sequence == message.metadata.sequence.consumer
assert delivery.redelivery_count == message.metadata.num_delivered - 1
```

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/unit/adapters/test_nats_delivery_mapping.py -q`

- [ ] **Step 3: Implement stream/consumer provisioning and explicit ACK/NAK**

Provisioning must be idempotent and validate incompatible existing consumer settings. Publish waits for PubAck. Empty fetch timeout returns `None`, not an exception.

- [ ] **Step 4: Run real JetStream integration**

```powershell
docker compose up -d nats
uv run pytest -m integration tests/integration/test_nats_jetstream_adapter.py -q
uv run pytest tests/contract/test_task_queue_contract.py -q
```

Assert durable redelivery after missing ACK, NAK delay, ACK removal and duplicate publish preservation.

- [ ] **Step 5: Commit**

```powershell
git add src/rag_mvp/adapters/message_queue/nats_jetstream.py tests/unit/adapters/test_nats_delivery_mapping.py tests/integration/test_nats_jetstream_adapter.py tests/contract/test_task_queue_contract.py tests/TEST.md docs/plans/milestone-d-real-infrastructure.md
git commit -m "feat(queue): 实现 JetStream 持久任务队列"
```

---

## Task 11：装配真实 Server、Worker 与 Outbox 循环

**Files:**
- Modify: `src/rag_mvp/bootstrap/container.py`
- Modify: `src/rag_mvp/rpc/server.py`
- Modify: `src/rag_mvp/ingestion/worker.py`
- Modify: `src/rag_mvp/outbox/main.py`
- Modify: `src/rag_mvp/outbox/finalizer.py`
- Modify: `src/rag_mvp/outbox/relay.py`
- Modify: `src/rag_mvp/outbox/sweeper.py`
- Modify: `src/rag_mvp/dev/cli.py`
- Create: `tests/unit/test_container_roles.py`
- Modify: `tests/unit/test_process_lifecycle.py`
- Modify: `tests/TEST.md`

**Interfaces:**
- Produces: `async build_server_container(settings) -> Container`
- Produces: `async build_worker_container(settings) -> Container`
- Produces: `async build_outbox_container(settings) -> Container`
- Changes: `run_worker` loops `worker_once` with bounded idle wait until stop
- Changes: Finalizer/Relay/Sweeper loops receive concrete ports and configured intervals

- [ ] **Step 1: Write failing role-composition tests**

Use injected adapter factories to assert each role only builds allowed dependencies, RagService has all application services, close is reverse-order and idempotent, and imports still cause no network connections.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/unit/test_container_roles.py tests/unit/test_process_lifecycle.py -q`

- [ ] **Step 3: Implement role-specific async composition**

Do not import `tests.fakes`. Server gets Document/Job/Retrieval; Worker gets queue/metadata/ingestion/cleanup; Outbox gets metadata/storage/queue. Parser Router includes four production parsers.

- [ ] **Step 4: Implement production loops and graceful shutdown**

Loops must use `asyncio.wait_for(stop_event.wait(), timeout=interval)` instead of unbounded sleep, so SIGTERM exits promptly. ACK/NAK remains only in `worker_once`.

- [ ] **Step 5: Verify GREEN**

```powershell
uv run pytest tests/unit/test_container_roles.py tests/unit/test_process_lifecycle.py tests/unit/test_import_boundaries.py -q
uv run pytest tests/unit tests/contract tests/functional -q
uv run ruff check src tests
uv run mypy src
```

- [ ] **Step 6: Commit**

```powershell
git add src/rag_mvp/bootstrap/container.py src/rag_mvp/rpc/server.py src/rag_mvp/ingestion/worker.py src/rag_mvp/outbox/main.py src/rag_mvp/outbox/finalizer.py src/rag_mvp/outbox/relay.py src/rag_mvp/outbox/sweeper.py src/rag_mvp/dev/cli.py tests/unit/test_container_roles.py tests/unit/test_process_lifecycle.py tests/TEST.md docs/plans/milestone-d-real-infrastructure.md
git commit -m "feat(bootstrap): 装配真实 RAG 进程依赖"
```

---

## Task 12：构建生产/测试镜像与完整 Compose 拓扑

**Files:**
- Modify: `Dockerfile`
- Create: `.dockerignore`
- Modify: `docker-compose.yml`
- Create: `scripts/docker_healthcheck.py`
- Create: `scripts/check_secret_leaks.py`
- Create: `tests/contract/test_container_artifacts.py`
- Modify: `docs/README.md`
- Modify: `docs/testing-guide.md`
- Modify: `tests/TEST.md`

**Interfaces:**
- Produces Docker targets: `runtime`, `test`
- Produces services: `rag-migrate`, `rag-server`, `rag-worker`, `rag-outbox`, profile `rag-test`
- Produces command: `uv run python scripts/docker_healthcheck.py`

- [ ] **Step 1: Write failing artifact/topology contract tests**

Assert runtime build context excludes `.env`, `tests`, `.git`, `data`, caches/logs; Compose includes migration ordering, shared object volume, health dependencies and only Server/Worker/test receive embedding variables.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/contract/test_container_artifacts.py -q`

- [ ] **Step 3: Implement multi-stage image and Compose**

`rag-migrate` must complete before application services. `rag-test` uses the test target and same Compose network. Never run `docker compose config` without `--quiet` because rendered output may contain Secret values.

- [ ] **Step 4: Build and health-check without exposing secrets**

```powershell
docker compose config --quiet
docker compose build rag-server rag-worker rag-outbox
docker compose up -d mysql elasticsearch nats
docker compose run --rm rag-migrate
docker compose up -d rag-server rag-worker rag-outbox
uv run python scripts/docker_healthcheck.py
```

- [ ] **Step 5: Inspect runtime image contents and logs**

Assert `/app/tests` and `/app/.env` do not exist. Pipe `docker compose logs --no-color` to `scripts/check_secret_leaks.py`; the script reads the configured key, returns non-zero on a match, and prints only `secret leak detected` without echoing the secret.

- [ ] **Step 6: Commit**

```powershell
git add Dockerfile .dockerignore docker-compose.yml scripts/docker_healthcheck.py scripts/check_secret_leaks.py tests/contract/test_container_artifacts.py docs/README.md docs/testing-guide.md tests/TEST.md docs/plans/milestone-d-real-infrastructure.md
git commit -m "build(docker): 建立真实 RAG Compose 拓扑"
```

---

## Task 13：新增真实 Docker gRPC 四格式 E2E

**Files:**
- Create: `tests/e2e/conftest.py`
- Create: `tests/e2e/test_real_upload_ingest_retrieve.py`
- Create: `tests/fixtures/documents/knowledge.txt`
- Create: `tests/fixtures/documents/guide.md`
- Create: `tests/fixtures/documents/sample.py`
- Create: `tests/fixtures/documents/manual.pdf` through a deterministic fixture builder
- Create: `scripts/build_test_fixtures.py`
- Modify: `tests/TEST.md`
- Modify: `docs/testing-guide.md`

**Interfaces:**
- Produces generated-gRPC-only E2E helpers: `create_dataset`, `submit_document`, `wait_for_job`, `retrieve`
- Does not import `rag_mvp.application` or `tests.fakes`

- [ ] **Step 1: Write failing real E2E**

For each of TXT/Markdown/Python/PDF, submit through generated client, poll `GetJob` until SUCCEEDED, call Retrieve, and assert returned Evidence belongs to the created Dataset/Document, has active index version, non-empty stage scores and precise line/page locator.

- [ ] **Step 2: Verify RED in test container**

Run:

```powershell
docker compose --profile test run --rm rag-test uv run pytest -m e2e tests/e2e/test_real_upload_ingest_retrieve.py -q
```

Expected: fails at the first incomplete production wiring or E2E assertion, never because a Fake is unavailable.

- [ ] **Step 3: Stop on production defects and add a targeted plan amendment**

This task owns only E2E fixtures/helpers/tests/docs. If RED exposes a production defect, record its exact failing boundary in this plan, implement the fix test-first in the owning adapter as a separate `fix(<scope>)` commit, then return to this task. Do not stage production `src/` files in the E2E commit and do not weaken polling, status or evidence assertions.

- [ ] **Step 4: Verify GREEN and cross-storage facts**

Run the E2E twice. Assert the second run uses fresh idempotency keys but does not leave stale PENDING Jobs; use read-only diagnostics to verify MySQL active version, ES document count and NATS pending/ack state.

- [ ] **Step 5: Commit**

```powershell
git add tests/e2e/conftest.py tests/e2e/test_real_upload_ingest_retrieve.py tests/fixtures/documents/knowledge.txt tests/fixtures/documents/guide.md tests/fixtures/documents/sample.py tests/fixtures/documents/manual.pdf scripts/build_test_fixtures.py tests/TEST.md docs/testing-guide.md docs/plans/milestone-d-real-infrastructure.md
git commit -m "test(e2e): 验证真实模型四格式 RAG 闭环"
```

Before commit, confirm `git diff --cached --name-only` contains only production corrections directly required by this E2E and their targeted tests.

---

## Task 14：新增 Docker KILL、redelivery 与并发恢复测试

**Files:**
- Create: `src/rag_mvp/ingestion/failpoints.py`
- Modify: `src/rag_mvp/config.py`
- Modify: `src/rag_mvp/bootstrap/container.py`
- Create: `tests/unit/ingestion/test_failpoints.py`
- Create: `tests/resilience/docker/conftest.py`
- Create: `tests/resilience/docker/test_worker_kill_recovery.py`
- Create: `tests/resilience/docker/test_relay_nats_recovery.py`
- Create: `tests/resilience/docker/test_real_concurrency_fences.py`
- Modify: `tests/fixtures/reliability_matrix.json`
- Modify: `tests/TEST.md`
- Modify: `docs/testing-guide.md`

**Interfaces:**
- Produces: `FileBarrierFailpoint(root: Path, enabled_checkpoints: set[Checkpoint])`
- Security: construction allowed only when `Settings.environment == TEST`

- [ ] **Step 1: Write failing failpoint safety/unit tests**

Assert production environment rejects fault injection, test environment writes a reached marker and blocks until a release marker exists, and cancellation closes without hanging.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/unit/ingestion/test_failpoints.py -q`

- [ ] **Step 3: Implement gated file barrier and test image wiring**

Runtime production services must not enable it. Docker resilience profile mounts only a dedicated barrier volume and sets `RAG_ENVIRONMENT=test` plus explicit checkpoint names.

- [ ] **Step 4: Write and run Worker/Relay recovery scenarios**

Commands are issued by pytest through Docker CLI:

```text
wait for barrier → docker kill --signal=KILL rag-worker → remove release marker
→ docker compose up -d rag-worker → poll terminal state → assert one visible ES version
```

Cover after-index-before-complete, after-success-before-ACK, Relay publish-before-mark and NATS stop/start READY-Outbox recovery. Never remove volumes.

- [ ] **Step 5: Run concurrent upload/Retry/rebuild/Delete fences**

Run: `docker compose --profile test run --rm rag-test uv run pytest -m docker_resilience tests/resilience/docker -q`

Expected: all scenarios converge; no Document resurrection, duplicate active retry or duplicate visible Chunk.

- [ ] **Step 6: Update T1～T25 real evidence and commit**

```powershell
git add src/rag_mvp/ingestion/failpoints.py src/rag_mvp/config.py src/rag_mvp/bootstrap/container.py tests/resilience/docker/conftest.py tests/resilience/docker/test_worker_kill_recovery.py tests/resilience/docker/test_relay_nats_recovery.py tests/resilience/docker/test_real_concurrency_fences.py tests/unit/ingestion/test_failpoints.py tests/fixtures/reliability_matrix.json tests/TEST.md docs/testing-guide.md docs/plans/milestone-d-real-infrastructure.md
git commit -m "test(resilience): 验证 Docker 强杀与真实投递恢复"
```

---

## Task 15：真实检索评测、CI 门禁与发布文档收敛

**Files:**
- Create: `tests/eval/test_real_retrieval_quality.py`
- Create: `.github/workflows/docker-quality.yml`
- Modify: `.github/workflows/quality.yml`
- Modify: `.githooks/pre-commit`
- Modify: `docs/SPEC.md`
- Modify: `docs/PLAN.md`
- Modify: `docs/README.md`
- Modify: `docs/testing-guide.md`
- Modify: `docs/plans/milestone-d-real-infrastructure.md`
- Modify: `docs/superpowers/specs/2026-08-24-real-infrastructure-rag-design.md`
- Modify: `tests/TEST.md`

**Interfaces:**
- Produces PR quick job, secret-backed Docker integration/E2E job and nightly Docker resilience/eval job
- Preserves no-network pre-commit behavior

- [ ] **Step 1: Write failing real retrieval evaluation**

Use the fixed 30-question corpus through gRPC/real ES/real model and compute existing `evaluate_rankings` metrics. Assert `Recall@6 >= 0.85`, `MRR@6 >= 0.70`, locator accuracy `== 1.0`; do not snapshot vectors or free text.

- [ ] **Step 2: Run real eval and diagnose quality failures without weakening thresholds**

Run:

```powershell
docker compose --profile test run --rm rag-test uv run pytest -m eval tests/eval/test_real_retrieval_quality.py -q
```

If a query fails, inspect Dense/BM25/RRF stage scores and fixture relevance. Production ranking changes require a targeted algorithm test; fixture labels change only when demonstrably incorrect.

- [ ] **Step 3: Add CI workflows and secret safety**

Docker job receives GitHub Secrets as environment values, runs `docker compose config --quiet`, adapter integration, model integration and E2E. Nightly job runs Docker resilience and real eval. Missing required Secret makes the selected job fail with a named configuration error.

- [ ] **Step 4: Run complete local release gate**

```powershell
uv run ruff check src tests scripts migrations
uv run ruff format --check src tests scripts migrations
uv run mypy src scripts migrations
uv run python scripts/check_generated.py
uv run pytest tests/unit tests/contract tests/functional tests/resilience tests/eval --cov=rag_mvp.domain --cov=rag_mvp.application --cov=rag_mvp.ingestion --cov=rag_mvp.retrieval --cov-fail-under=85
docker compose --profile test run --rm rag-test uv run pytest -m "integration or model_integration or e2e" tests/integration tests/e2e -q
docker compose --profile test run --rm rag-test uv run pytest -m docker_resilience tests/resilience/docker -q
docker compose --profile test run --rm rag-test uv run pytest -m eval tests/eval/test_real_retrieval_quality.py -q
```

- [ ] **Step 5: Reconcile documentation against actual evidence**

Only when every command above passes:

- mark T1～T25 real validation complete;
- change PLAN status to “Milestone D 真实可靠发布基线通过”;
- change design status to “已实施并验收”;
- record exact model name/dimension without API Key;
- record Docker/ES/MySQL/NATS versions and test counts.

If any required command remains unrun or failed, keep the release status unapproved and list the exact blocker.

- [ ] **Step 6: Commit final gate**

```powershell
git add .github/workflows/docker-quality.yml .github/workflows/quality.yml .githooks/pre-commit tests/eval/test_real_retrieval_quality.py tests/TEST.md docs/SPEC.md docs/PLAN.md docs/README.md docs/testing-guide.md docs/plans/milestone-d-real-infrastructure.md docs/superpowers/specs/2026-08-24-real-infrastructure-rag-design.md
git commit -m "ci: 建立真实 RAG 发布验收门禁"
```

---

## Execution Rules

1. Start each Task by re-reading its Files/Interfaces and current `git status`.
2. Preserve the existing uncommitted `.env.example` user change until Task 3 deliberately incorporates it; never stage `.env`.
3. Record each RED failure and GREEN command result under the task checkbox before commit.
4. A task is not complete while a required check fails, its test index is stale, or generated/migration artifacts are missing.
5. After every commit report commit hash, included module, commands run and commands not run.
6. Do not push. Do not start Milestone E/Go work.

## Spec Coverage Self-Review

| Design/SPEC area | Implemented by |
|---|---|
| Cross-platform proto gate | Task 1 |
| Runtime dependencies and explicit production config | Tasks 2～3 |
| MySQL schema, transactions, Outbox, lifecycle and generation fences | Tasks 4～7 |
| Real OpenAI-compatible Embedding and model safety | Task 8 |
| Elasticsearch Dense/BM25/versioned deletion | Task 9 |
| JetStream durable ACK/NAK/redelivery | Task 10 |
| Role-specific production composition and loops | Task 11 |
| Runtime/test images, Compose topology and secret boundary | Task 12 |
| Four-format real gRPC RAG path | Task 13 |
| T1～T25 real recovery/concurrency evidence and Docker KILL | Task 14 |
| Real Recall/MRR, CI and release-status reconciliation | Task 15 |

No in-scope design requirement is uncovered. Go/Agent/SSE, OCR, MinIO, Kubernetes and multi-model-in-one-index remain explicitly out of scope.
