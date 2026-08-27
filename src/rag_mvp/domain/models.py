"""Infrastructure-neutral RAG domain models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType

from rag_mvp.domain.enums import (
    DatasetStatus,
    DocumentStatus,
    FingerprintState,
    IndexBuildStatus,
    JobStatus,
    JobType,
    OutboxStatus,
    TaskStatus,
    TaskType,
)
from rag_mvp.domain.errors import DomainFailure


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _require_digest(value: str, field_name: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 hex digest")


def _frozen_mapping(value: Mapping[str, str]) -> Mapping[str, str]:
    return MappingProxyType(dict(value))


@dataclass(frozen=True, slots=True)
class Dataset:
    id: str
    name: str
    embedding_model: str
    embedding_dimension: int
    created_at: datetime
    search_schema_version: int = 1
    tenant_id: str = "default_tenant"
    status: DatasetStatus = DatasetStatus.ACTIVE
    lifecycle_generation: int = 0

    def __post_init__(self) -> None:
        _require_text(self.id, "id")
        _require_text(self.name, "name")
        _require_text(self.embedding_model, "embedding_model")
        _require_text(self.tenant_id, "tenant_id")
        if self.embedding_dimension < 1:
            raise ValueError("embedding_dimension must be at least 1")
        if self.search_schema_version < 1:
            raise ValueError("search_schema_version must be at least 1")
        if self.lifecycle_generation < 0:
            raise ValueError("lifecycle_generation must not be negative")


@dataclass(frozen=True, slots=True)
class Document:
    id: str
    dataset_id: str
    source_name: str
    file_sha256: str
    status: DocumentStatus
    active_version: int | None
    next_index_version: int
    lifecycle_generation: int
    created_at: datetime
    object_key: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.id, "id")
        _require_text(self.dataset_id, "dataset_id")
        _require_text(self.source_name, "source_name")
        _require_digest(self.file_sha256, "file_sha256")
        if self.active_version is not None and self.active_version < 1:
            raise ValueError("active_version must be at least 1")
        if self.next_index_version < 1:
            raise ValueError("next_index_version must be at least 1")
        if self.lifecycle_generation < 0:
            raise ValueError("lifecycle_generation must not be negative")


@dataclass(frozen=True, slots=True)
class IngestionFingerprint:
    dataset_id: str
    file_sha256: str
    config_digest: str
    document_id: str
    job_id: str
    state: FingerprintState

    def __post_init__(self) -> None:
        _require_digest(self.file_sha256, "file_sha256")
        _require_digest(self.config_digest, "config_digest")


@dataclass(frozen=True, slots=True)
class Job:
    id: str
    type: JobType
    document_id: str | None
    config_digest: str
    index_version: int
    document_generation: int
    status: JobStatus
    progress: float
    created_at: datetime
    error: DomainFailure | None = None
    retryable: bool = False
    retry_count: int = 0
    cancel_requested_at: datetime | None = None
    retry_of_job_id: str | None = None
    is_system: bool = False
    dataset_id: str = ""

    def __post_init__(self) -> None:
        _require_text(self.dataset_id, "dataset_id")
        _require_digest(self.config_digest, "config_digest")
        if self.index_version < 1:
            raise ValueError("index_version must be at least 1")
        if self.document_generation < 0:
            raise ValueError("document_generation must not be negative")
        if not 0.0 <= self.progress <= 1.0:
            raise ValueError("progress must be between 0 and 1")
        if self.retry_count < 0:
            raise ValueError("retry_count must not be negative")
        if self.type is JobType.DELETE_DATASET:
            if not self.dataset_id:
                raise ValueError("dataset_id is required for dataset cleanup")
            if self.document_id is not None:
                raise ValueError("dataset cleanup must not reference a document")


@dataclass(frozen=True, slots=True)
class Task:
    id: str
    job_id: str
    type: TaskType
    status: TaskStatus
    attempt: int
    last_delivery_sequence: int | None
    checkpoint: str | None
    created_at: datetime
    error: DomainFailure | None = None

    def __post_init__(self) -> None:
        if self.attempt < 0:
            raise ValueError("attempt must not be negative")
        if self.last_delivery_sequence is not None and self.last_delivery_sequence < 1:
            raise ValueError("last_delivery_sequence must be at least 1")


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    id: str
    task_id: str
    status: OutboxStatus
    attempt: int
    staging_key: str | None
    created_at: datetime
    published_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.attempt < 0:
            raise ValueError("attempt must not be negative")


@dataclass(frozen=True, slots=True)
class IndexBuild:
    document_id: str
    index_version: int
    job_id: str
    status: IndexBuildStatus
    created_at: datetime

    def __post_init__(self) -> None:
        if self.index_version < 1:
            raise ValueError("index_version must be at least 1")


@dataclass(frozen=True, slots=True)
class Locator:
    page_number: int | None = None
    start_line: int | None = None
    end_line: int | None = None
    symbol: str | None = None
    language: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.page_number is not None and self.page_number < 1:
            raise ValueError("page_number must be at least 1")
        if self.start_line is not None and self.start_line < 1:
            raise ValueError("start_line must be at least 1")
        if self.end_line is not None and self.end_line < 1:
            raise ValueError("end_line must be at least 1")
        object.__setattr__(self, "metadata", _frozen_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class Chunk:
    id: str
    document_id: str
    index_version: int
    ordinal: int
    content_with_weight: str
    content_sha256: str
    source_name: str
    locator: Locator
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.index_version < 1:
            raise ValueError("index_version must be at least 1")
        if self.ordinal < 0:
            raise ValueError("ordinal must not be negative")
        _require_text(self.content_with_weight, "content_with_weight")
        _require_digest(self.content_sha256, "content_sha256")
        object.__setattr__(self, "metadata", _frozen_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class ScoreBreakdown:
    dense_score: float | None = None
    sparse_score: float | None = None
    fusion_score: float | None = None
    rerank_score: float | None = None


@dataclass(frozen=True, slots=True)
class Evidence:
    chunk_id: str
    document_id: str
    content_with_weight: str
    source_name: str
    locator: Locator
    scores: ScoreBreakdown
    index_version: int
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.index_version < 1:
            raise ValueError("index_version must be at least 1")
        object.__setattr__(self, "metadata", _frozen_mapping(self.metadata))
