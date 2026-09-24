"""元数据持久化能力边界：定义权威状态读取、行锁与事务性任务创建需求。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from rag_mvp.domain.errors import DomainFailure
from rag_mvp.domain.models import Chunk, Dataset, Document, Job, OutboxEvent, Task


@dataclass(frozen=True, slots=True)
class SubmitIngestion:
    idempotency_key: str
    dataset_id: str
    source_name: str
    staging_key: str
    file_sha256: str
    config_digest: str
    now: datetime
    target_document_id: str | None = None


@dataclass(frozen=True, slots=True)
class SubmitResult:
    document_id: str
    job_id: str
    task_id: str
    reused: bool
    staging_referenced: bool


@dataclass(frozen=True, slots=True)
class TaskClaim:
    task: Task
    job: Job
    dataset: Dataset
    document: Document | None


@dataclass(frozen=True, slots=True)
class RetryJobRequest:
    idempotency_key: str
    job_id: str
    now: datetime
    max_user_retries: int


@dataclass(frozen=True, slots=True)
class RetryJobResult:
    job_id: str
    task_id: str
    reused: bool


@dataclass(frozen=True, slots=True)
class ReindexDocumentRequest:
    idempotency_key: str
    document_id: str
    config_digest: str
    now: datetime


@dataclass(frozen=True, slots=True)
class ReindexDocumentResult:
    document_id: str
    job_id: str
    task_id: str
    reused: bool


@dataclass(frozen=True, slots=True)
class DeleteDocumentRequest:
    idempotency_key: str
    document_id: str
    now: datetime


@dataclass(frozen=True, slots=True)
class DeleteDocumentResult:
    document_id: str
    job_id: str
    task_id: str
    reused: bool


@dataclass(frozen=True, slots=True)
class DeleteDatasetRequest:
    idempotency_key: str
    dataset_id: str
    now: datetime


@dataclass(frozen=True, slots=True)
class DeleteDatasetResult:
    dataset_id: str
    job_id: str
    task_id: str
    reused: bool


@dataclass(frozen=True, slots=True)
class CancelJobRequest:
    idempotency_key: str
    job_id: str
    now: datetime


@dataclass(frozen=True, slots=True)
class CancelJobResult:
    job_id: str
    reused: bool


class MetadataRepository(Protocol):
    """Persist authoritative RAG metadata and conditional state changes."""

    # 按数据集 ID 和幂等语义持久化数据集，并返回最终数据集。
    async def create_dataset(self, dataset: Dataset) -> Dataset: ...

    # 按 ID 读取数据集；不存在时返回 None。
    async def get_dataset(self, dataset_id: str) -> Dataset | None: ...

    async def bind_embedding_profile(
        self, dataset_id: str, model: str, dimension: int, encrypted_profile: str
    ) -> Dataset: ...

    # 在同一事务创建或复用摄取 Document、Job、Task 和 OutboxEvent。
    async def submit_ingestion(self, command: SubmitIngestion) -> SubmitResult: ...

    # 按 ID 读取 Job；不存在时返回 None。
    async def get_job(self, job_id: str) -> Job | None: ...

    # 按 ID 读取 Task；不存在时返回 None。
    async def get_task(self, task_id: str) -> Task | None: ...

    # 读取 Job 的唯一 Task；暂未建立时返回 None。
    async def get_task_for_job(self, job_id: str) -> Task | None: ...

    # 按 ID 读取 Document；不存在时返回 None。
    async def get_document(self, document_id: str) -> Document | None: ...

    # 列出仍引用 staging object 的 WAITING_OBJECT OutboxEvent。
    async def list_waiting_outbox(self, limit: int) -> Sequence[OutboxEvent]: ...

    # 在 Outbox 仍等待对象且 Document 未删除时，写入正式对象 key 并置为可发布。
    async def mark_object_ready(self, event_id: str, object_key: str, now: datetime) -> bool: ...

    # 递增对象提升失败次数；达到上限时使关联 Task/Job 失败并取消 Outbox。
    async def record_finalization_failure(
        self, event_id: str, max_attempts: int, now: datetime
    ) -> bool: ...

    # 返回仍被 WAITING_OBJECT OutboxEvent 引用的 staging object key。
    async def waiting_staging_keys(self) -> Sequence[str]: ...

    # 列出可发布到任务队列的 READY_TO_PUBLISH OutboxEvent。
    async def list_ready_outbox(self, limit: int) -> Sequence[OutboxEvent]: ...

    # 在 Outbox 仍可发布时记录发布时间并置为 PUBLISHED。
    async def mark_outbox_published(self, event_id: str, now: datetime) -> bool: ...

    # 按投递序号认领可执行 Task，并同时验证取消标记和文档 generation fence。
    async def claim_task(
        self, task_id: str, delivery_sequence: int, now: datetime
    ) -> TaskClaim | None: ...

    # 在 Task 仍为 RUNNING 时推进 Job 进度，让长时间摄取对用户可见。
    async def set_job_progress(self, task_id: str, progress: float, now: datetime) -> bool: ...

    # 在认领状态和 generation fence 仍有效时持久化 Chunk 并完成摄取 Job/Task。
    async def complete_ingestion(
        self, task_id: str, chunks: Sequence[Chunk], now: datetime
    ) -> bool: ...

    # 将仍可失败的 Task 和关联 Job 标记为不可重试的领域失败。
    async def fail_task(self, task_id: str, failure: DomainFailure, now: datetime) -> bool: ...

    # 在失败 Job 行锁下创建或复用一个受最大次数限制的重试 Job/Task/Outbox。
    async def retry_job(self, request: RetryJobRequest) -> RetryJobResult: ...

    # 为已有正式对象创建一个新的完整索引版本；旧 active_version 在成功前保持可见。
    async def reindex_document(self, request: ReindexDocumentRequest) -> ReindexDocumentResult: ...

    # 逻辑删除文档、递增 generation，并创建异步清理 Job/Task。
    async def delete_document(self, request: DeleteDocumentRequest) -> DeleteDocumentResult: ...

    # 将数据集置为删除中，并创建异步清理其文档、索引和对象的 Job/Task。
    async def delete_dataset(self, request: DeleteDatasetRequest) -> DeleteDatasetResult: ...

    # 返回数据集清理 Task 需要删除的正式对象 key。
    async def dataset_cleanup_object_keys(self, task_id: str) -> Sequence[str]: ...

    # 在清理 Task 仍有效时完成数据集物理清理及其 Job/Task 状态。
    async def finalize_dataset_cleanup(self, task_id: str, now: datetime) -> bool: ...

    # 在 Task 状态和 generation fence 仍有效时完成普通清理 Job/Task。
    async def complete_cleanup(self, task_id: str, now: datetime) -> bool: ...

    # 按幂等键请求取消摄取 Job，并返回实际被取消或复用的结果。
    async def cancel_job(self, request: CancelJobRequest) -> CancelJobResult: ...

    # 返回仍可检索的文档及其 active index version，排除已删除和无活跃版本文档。
    async def visible_document_versions(self, document_ids: Sequence[str]) -> Mapping[str, int]: ...
