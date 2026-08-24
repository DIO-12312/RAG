"""Transport-independent application DTOs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


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
    embedding_model: str
    now: datetime


@dataclass(frozen=True, slots=True)
class SubmitDocumentResult:
    document_id: str
    job_id: str
    reused: bool
