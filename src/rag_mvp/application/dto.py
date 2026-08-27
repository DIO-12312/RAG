"""Transport-independent application DTOs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from rag_mvp.domain.enums import JobStatus, JobType, TaskStatus
from rag_mvp.domain.errors import DomainFailure


@dataclass(frozen=True, slots=True)
class CreateDatasetCommand:
    request_id: str
    idempotency_key: str
    name: str
    embedding_model: str
    embedding_dimension: int
    now: datetime
    dataset_id: str | None = None


@dataclass(frozen=True, slots=True)
class CreateDatasetResult:
    dataset_id: str
    name: str
    embedding_model: str
    embedding_dimension: int


@dataclass(frozen=True, slots=True)
class SubmitDocumentCommand:
    request_id: str
    idempotency_key: str
    dataset_id: str
    source_name: str
    content: bytes
    expected_sha256: str | None
    target_document_id: str | None
    parser_version: str
    chunk_size: int
    chunk_overlap: int
    embedding_model: str | None
    now: datetime


@dataclass(frozen=True, slots=True)
class SubmitDocumentResult:
    document_id: str
    job_id: str
    reused: bool


@dataclass(frozen=True, slots=True)
class GetJobQuery:
    request_id: str
    job_id: str


@dataclass(frozen=True, slots=True)
class RetryJobCommand:
    request_id: str
    idempotency_key: str
    job_id: str
    now: datetime


@dataclass(frozen=True, slots=True)
class DeleteDocumentCommand:
    request_id: str
    idempotency_key: str
    document_id: str
    now: datetime


@dataclass(frozen=True, slots=True)
class DeleteDocumentResult:
    document_id: str
    job_id: str
    reused: bool


@dataclass(frozen=True, slots=True)
class DeleteDatasetCommand:
    request_id: str
    idempotency_key: str
    dataset_id: str
    now: datetime


@dataclass(frozen=True, slots=True)
class DeleteDatasetResult:
    dataset_id: str
    job_id: str
    reused: bool


@dataclass(frozen=True, slots=True)
class CancelJobCommand:
    request_id: str
    idempotency_key: str
    job_id: str
    now: datetime


@dataclass(frozen=True, slots=True)
class JobView:
    job_id: str
    document_id: str | None
    dataset_id: str
    type: JobType
    status: JobStatus
    progress: float
    failure: DomainFailure | None
    retryable: bool
    retry_count: int
    cancel_requested: bool
    task_status: TaskStatus


@dataclass(frozen=True, slots=True)
class RetrieveQuery:
    request_id: str
    dataset_id: str
    query: str
    top_k: int
    filters: Mapping[str, str]
    max_context_tokens: int
    enable_rerank: bool = False
