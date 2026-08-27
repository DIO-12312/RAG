"""SQLAlchemy 行与基础设施无关领域模型之间的转换，隔离持久化细节。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from rag_mvp.adapters.metadata.tables import (
    DatasetTable,
    DocumentTable,
    JobTable,
    OutboxEventTable,
    TaskTable,
)
from rag_mvp.domain.enums import (
    DatasetStatus,
    DocumentStatus,
    JobStatus,
    JobType,
    OutboxStatus,
    TaskStatus,
    TaskType,
)
from rag_mvp.domain.errors import DomainFailure
from rag_mvp.domain.models import Dataset, Document, Job, OutboxEvent, Task


# 转换该方法负责的领域数据或基础设施状态。
def as_utc(value: datetime) -> datetime:
    """Restore UTC tzinfo stripped by MySQL DATETIME columns."""

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


# 实现 failure_from_json 对应的局部职责。
def failure_from_json(value: Mapping[str, Any] | None) -> DomainFailure | None:
    if value is None:
        return None
    return DomainFailure(
        code=str(value["code"]),
        message=str(value["message"]),
        retryable=bool(value.get("retryable", False)),
    )


# 实现 failure_to_json 对应的局部职责。
def failure_to_json(value: DomainFailure | None) -> dict[str, object] | None:
    if value is None:
        return None
    return {
        "code": value.code,
        "message": value.message,
        "retryable": value.retryable,
    }


# 实现 dataset_from_table 对应的局部职责。
def dataset_from_table(row: DatasetTable) -> Dataset:
    return Dataset(
        id=row.id,
        tenant_id=row.tenant_id,
        name=row.name,
        embedding_model=row.embedding_model,
        embedding_dimension=row.embedding_dimension,
        search_schema_version=row.search_schema_version,
        status=DatasetStatus(row.status),
        lifecycle_generation=row.lifecycle_generation,
        created_at=as_utc(row.created_at),
    )


# 实现 document_from_table 对应的局部职责。
def document_from_table(row: DocumentTable) -> Document:
    return Document(
        id=row.id,
        dataset_id=row.dataset_id,
        source_name=row.source_name,
        file_sha256=row.file_sha256,
        status=DocumentStatus(row.status),
        active_version=row.active_version,
        next_index_version=row.next_index_version,
        lifecycle_generation=row.lifecycle_generation,
        object_key=row.object_key,
        created_at=as_utc(row.created_at),
    )


# 实现 job_from_table 对应的局部职责。
def job_from_table(row: JobTable) -> Job:
    return Job(
        id=row.id,
        type=JobType(row.type),
        document_id=row.document_id,
        dataset_id=row.dataset_id,
        config_digest=row.config_digest,
        index_version=row.index_version,
        document_generation=row.document_generation,
        status=JobStatus(row.status),
        progress=float(row.progress),
        error=failure_from_json(row.error),
        retryable=row.retryable,
        retry_count=row.retry_count,
        cancel_requested_at=(
            as_utc(row.cancel_requested_at) if row.cancel_requested_at is not None else None
        ),
        retry_of_job_id=row.retry_of_job_id,
        is_system=row.is_system,
        created_at=as_utc(row.created_at),
    )


# 实现 task_from_table 对应的局部职责。
def task_from_table(row: TaskTable) -> Task:
    return Task(
        id=row.id,
        job_id=row.job_id,
        type=TaskType(row.type),
        status=TaskStatus(row.status),
        attempt=row.attempt,
        last_delivery_sequence=row.last_delivery_sequence,
        checkpoint=row.checkpoint,
        error=failure_from_json(row.error),
        created_at=as_utc(row.created_at),
    )


# 实现 outbox_from_table 对应的局部职责。
def outbox_from_table(row: OutboxEventTable) -> OutboxEvent:
    return OutboxEvent(
        id=row.id,
        task_id=row.task_id,
        status=OutboxStatus(row.status),
        attempt=row.attempt,
        staging_key=row.staging_key,
        published_at=as_utc(row.published_at) if row.published_at is not None else None,
        created_at=as_utc(row.created_at),
    )
