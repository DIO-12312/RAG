# DeleteDataset 级联物理删除设计

日期：2026-08-27

状态：已批准，待实施

## 目标

提供版本化 gRPC `DeleteDataset`。它必须让 Dataset 在命令提交后立即不可提交、不可检索，并通过既有 Outbox、NATS JetStream 和 Worker 可靠地清理 Elasticsearch 向量/BM25 文档、源对象和 MySQL 中该 Dataset 的全部业务数据。该能力既服务未来前后端，也让真实 eval 在结束时不积压一次性数据。

删除不可恢复；MVP 不保留墓碑、审计记录或完成后的 Job 历史。

## 已排除的方案

1. 直接在 RPC 中执行 MySQL/ES/对象删除：无法重试长耗时外部 I/O，也绕过 Outbox 与 Worker 的唯一消费边界。
2. 逐文档调用现有 `DeleteDocument`：无法原子地封闭 Dataset，清理完成也仍保留 Dataset、Document、Job 等 MySQL 行，不能满足一次性 eval 数据完全清空。
3. `docker compose down -v`：能清卷但会删除开发环境全部 MySQL/ES/NATS 数据，不能成为产品 API 或 eval 的常规清理方式。

选择 dataset 作用域 Job/Task：先在 MySQL 中完成可见性切换，再让 Worker 重试外部清理，最后物理删除整个聚合。

## 对外契约

```proto
service RagService {
  rpc DeleteDataset(DeleteDatasetRequest) returns (DeleteDatasetResponse);
}

enum JobType {
  JOB_TYPE_DELETE_DATASET = 4;
}

message DeleteDatasetRequest {
  RequestContext context = 1;
  string dataset_id = 2;
}

message DeleteDatasetResult {
  string dataset_id = 1;
  string job_id = 2;
}

message DeleteDatasetResponse {
  oneof outcome {
    DeleteDatasetResult result = 1;
    BusinessError error = 2;
  }
}
```

`JobResult` 新增 `dataset_id = 11`。为保持 proto 兼容，已有 `document_id` 字段不变；`DELETE_DATASET` Job 的 `document_id` 是空字符串。

所有调用必须带非空 `request_id` 与 `idempotency_key`。同 key 的重复调用在 Dataset 尚未物理删除时返回同一 `job_id`；同 key 指向不同 Dataset 返回 `IDEMPOTENCY_CONFLICT`。另一 key 在删除进行中返回 `DATASET_DELETION_IN_PROGRESS`。Dataset 已物理删除时返回 `DATASET_NOT_FOUND`。

`DeleteDatasetResult` 只表示删除已受理、数据已立即不可见，不表示基础设施清理完成。调用方可用 `GetJob` 观察 `PENDING/RUNNING/FAILED`；Worker 完成最终物理删除会一起删掉该 Job，因此先前获得的 `job_id` 随后返回 `JOB_NOT_FOUND`，这表示清空成功。调用方不得把 `JOB_NOT_FOUND` 当作失败重试删除。

## 数据模型与不变量

新增：

- `DatasetStatus = ACTIVE | DELETING`，`Dataset.status` 初始为 `ACTIVE`。
- `Dataset.lifecycle_generation: bigint`，初始为 0，只在删除开始时递增。
- `Job.dataset_id`，非空且外键关联 Dataset；已有 Job 同步回填其 Document 的 `dataset_id`。
- `Job.document_id` 改为可空：摄取、文档删除、索引版本清理仍非空；`DELETE_DATASET` 必须为空。
- `IdempotencyRecord.dataset_id`，非空且外键关联 Dataset；创建 Dataset 的幂等记录也写入新 Dataset ID。这样最终 purge 不依赖扫描 JSON 结果字段。
- `JobType.DELETE_DATASET` 与 `TaskType.CLEANUP_DATASET`。

`TaskClaim` 成为作用域联合体：始终返回 `dataset` 与 `job`；Document 作用域任务返回非空 `document`，dataset 清理任务的 `document` 为 `None`。摄取服务只接受非空 document，清理服务按 task type 分派。

所有 Dataset 读取都只返回 `ACTIVE`，或在内部需要时显式读取 `DELETING` 行锁。`SubmitDocument`、`Retrieve`、Document 重建和 `DeleteDocument` 对 `DELETING` 返回 `DATASET_DELETING`。`visible_document_versions` 必须额外筛选 `Dataset.status=ACTIVE`，确保 ES 的旧记录在删除受理后不再泄漏。

## 删除状态流与事务

```text
ACTIVE
  │ DeleteDataset（Dataset 行锁，同一 MySQL 事务）
  ▼
DELETING ──创建 DELETE_DATASET / CLEANUP_DATASET / READY Outbox──► Worker
  │                                                                  │
  │ 立即拒绝 Submit / Retrieve / Document 变更                       │ ES + objects 幂等删除
  │                                                                  ▼
  └──────────────────────── 外部失败：NAK / 重投 ─────────────► 原子物理删除全部聚合行
```

受理事务必须按以下顺序完成：

1. `SELECT ... FOR UPDATE` 锁定 Dataset；验证它是 `ACTIVE`，改为 `DELETING` 并递增 dataset generation。
2. 锁定该 Dataset 的 Document；全部标为 `DELETED` 并递增 document generation；所有 fingerprint 置为 `RELEASED`。
3. 取消所有未终态摄取、文档删除和版本清理 Job/Task；撤销其 `WAITING_OBJECT`/`READY_TO_PUBLISH` Outbox。已经 `PUBLISHED` 的消息不能撤回。
4. 创建 dataset 作用域 `DELETE_DATASET` Job、`CLEANUP_DATASET` Task 和 `READY_TO_PUBLISH` OutboxEvent，并写入操作幂等记录；三者仍在同一 MySQL 事务。

Worker 认领和完成都要校验 Dataset 为 `DELETING` 且 generation 等于 Job 记录的 generation。任何旧 document task 的认领/完成也必须额外复核 Dataset 仍为 `ACTIVE` 且 generation 相符；否则只能取消/ACK，绝不能切换 active version 或写入 READY。

## CleanupDataset 行为

1. 通过 `SearchEngine.delete_dataset(dataset_id)` 使用 ES `delete_by_query` 删除该 dataset 的全部物理 chunk；0 命中视为成功。
2. 从 MetadataRepository 在数据集清理快照中读取全部正式 `object_key`，逐个调用 `ObjectStorage.delete`；不存在对象视为成功。
3. 上述外部 I/O 都成功后，执行 `finalize_dataset_cleanup(task_id, now)`。它在一个 MySQL 事务内重新锁定 dataset cleanup Task/Job/Dataset，并检查 `DELETING` 与 generation fence。
4. 按外键由子到父删除该 Dataset 的 OutboxEvent、ChunkManifest、IndexBuild、Task、Job（先清空 retry self-reference）、IngestionFingerprint、Document、带 `dataset_id` 的操作幂等记录和 Dataset。最后一次事务提交就是“清理完成”信号。

任何外部异常映射为可重试 `DATASET_CLEANUP_RETRYABLE`，沿用 Worker 的 NAK/max-deliver 语义；未完成前 Dataset 继续为 `DELETING` 并保持不可见。清理 Job 不可 `CancelJob`、不可 `RetryJob`；若到 max-deliver 后失败，由运营/未来控制面以相同 Job 的受控恢复机制处理，绝不允许把 Dataset 改回 `ACTIVE`。

潜在的旧 NATS delivery 在最终 MySQL 删除后找不到 Task，Worker 只 ACK。这保证清理后不留下可重新执行的任务。

## 真实 eval teardown

`tests/e2e/conftest.py` 新增 generated-gRPC-only 的 `delete_dataset(stub, dataset_id) -> str` 和 `wait_for_dataset_purged(stub, job_id)`：后者先允许观察 `PENDING/RUNNING`，最终将 `JOB_NOT_FOUND` 解释为成功；若先观察 `FAILED/CANCELLED` 则失败。

两个真实 eval 用例以 `try/finally` 调用该 helper。评测日志必须在 teardown 前写出；即使指标断言失败，finally 仍须等待 Dataset purge。清理失败应让测试失败并保留日志，不能静默吞掉。此操作仅删除测试自己用随机 UUID 创建的 Dataset。

## 验收

- 新旧 proto 生成物、gRPC service、generated dev CLI 和 RPC contract 同步。
- Dataset 删除受理后，`Retrieve`、`SubmitDocument` 立即返回 `DATASET_DELETING` 或 `DATASET_NOT_FOUND`，ES 仍有物理记录也不可见。
- 并发 ingest、Finalizer、已发布 delivery 和 Dataset 删除不能复活任何 Document/IndexBuild。
- `CLEANUP_DATASET` 发生 ES 或对象异常后可幂等重投；成功时 ES、对象和 MySQL 全部无该 Dataset 的数据。
- 两个真实 eval 都在 finally 中调用删除；Docker eval 连续运行不累积 Dataset、Document、Job、Task、Outbox 或 ES chunk。
