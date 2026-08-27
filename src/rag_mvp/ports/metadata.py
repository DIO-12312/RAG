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

    # 创建该方法负责的领域数据或基础设施状态。
    async def create_dataset(self, dataset: Dataset) -> Dataset: ...

    # 读取该方法负责的领域数据或基础设施状态。
    async def get_dataset(self, dataset_id: str) -> Dataset | None: ...

    # 提交该方法负责的领域数据或基础设施状态。
    async def submit_ingestion(self, command: SubmitIngestion) -> SubmitResult: ...

    # 读取该方法负责的领域数据或基础设施状态。
    async def get_job(self, job_id: str) -> Job | None: ...

    # 读取该方法负责的领域数据或基础设施状态。
    async def get_task(self, task_id: str) -> Task | None: ...

    # 读取该方法负责的领域数据或基础设施状态。
    async def get_task_for_job(self, job_id: str) -> Task | None: ...

    # 读取该方法负责的领域数据或基础设施状态。
    async def get_document(self, document_id: str) -> Document | None: ...

    # 列出该方法负责的领域数据或基础设施状态。
    async def list_waiting_outbox(self, limit: int) -> Sequence[OutboxEvent]: ...

    # 条件更新该方法负责的领域数据或基础设施状态。
    async def mark_object_ready(self, event_id: str, object_key: str, now: datetime) -> bool: ...

    # 持久记录该方法负责的领域数据或基础设施状态。
    async def record_finalization_failure(
        self, event_id: str, max_attempts: int, now: datetime
    ) -> bool: ...

    # 实现 waiting_staging_keys 对应的局部职责。
    async def waiting_staging_keys(self) -> Sequence[str]: ...

    # 列出该方法负责的领域数据或基础设施状态。
    async def list_ready_outbox(self, limit: int) -> Sequence[OutboxEvent]: ...

    # 条件更新该方法负责的领域数据或基础设施状态。
    async def mark_outbox_published(self, event_id: str, now: datetime) -> bool: ...

    # 条件认领该方法负责的领域数据或基础设施状态。
    async def claim_task(
        self, task_id: str, delivery_sequence: int, now: datetime
    ) -> TaskClaim | None: ...

    # 条件完成该方法负责的领域数据或基础设施状态。
    async def complete_ingestion(
        self, task_id: str, chunks: Sequence[Chunk], now: datetime
    ) -> bool: ...

    # 记录失败该方法负责的领域数据或基础设施状态。
    async def fail_task(self, task_id: str, failure: DomainFailure, now: datetime) -> bool: ...

    # 重试该方法负责的领域数据或基础设施状态。
    async def retry_job(self, request: RetryJobRequest) -> RetryJobResult: ...

    # 删除该方法负责的领域数据或基础设施状态。
    async def delete_document(self, request: DeleteDocumentRequest) -> DeleteDocumentResult: ...

    # 删除该方法负责的领域数据或基础设施状态。
    async def delete_dataset(self, request: DeleteDatasetRequest) -> DeleteDatasetResult: ...

    # 实现 dataset_cleanup_object_keys 对应的局部职责。
    async def dataset_cleanup_object_keys(self, task_id: str) -> Sequence[str]: ...

    # 实现 finalize_dataset_cleanup 对应的局部职责。
    async def finalize_dataset_cleanup(self, task_id: str, now: datetime) -> bool: ...

    # 条件完成该方法负责的领域数据或基础设施状态。
    async def complete_cleanup(self, task_id: str, now: datetime) -> bool: ...

    # 取消该方法负责的领域数据或基础设施状态。
    async def cancel_job(self, request: CancelJobRequest) -> CancelJobResult: ...

    # 实现 visible_document_versions 对应的局部职责。
    async def visible_document_versions(self, document_ids: Sequence[str]) -> Mapping[str, int]: ...
