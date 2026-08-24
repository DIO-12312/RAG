"""Metadata persistence capability boundary."""

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
    document: Document


class MetadataRepository(Protocol):
    """Persist authoritative RAG metadata and conditional state changes."""

    async def create_dataset(self, dataset: Dataset) -> Dataset: ...

    async def get_dataset(self, dataset_id: str) -> Dataset | None: ...

    async def submit_ingestion(self, command: SubmitIngestion) -> SubmitResult: ...

    async def get_job(self, job_id: str) -> Job | None: ...

    async def get_task(self, task_id: str) -> Task | None: ...

    async def get_document(self, document_id: str) -> Document | None: ...

    async def list_waiting_outbox(self, limit: int) -> Sequence[OutboxEvent]: ...

    async def mark_object_ready(self, event_id: str, object_key: str, now: datetime) -> bool: ...

    async def list_ready_outbox(self, limit: int) -> Sequence[OutboxEvent]: ...

    async def mark_outbox_published(self, event_id: str, now: datetime) -> bool: ...

    async def claim_task(
        self, task_id: str, delivery_sequence: int, now: datetime
    ) -> TaskClaim | None: ...

    async def complete_ingestion(
        self, task_id: str, chunks: Sequence[Chunk], now: datetime
    ) -> bool: ...

    async def fail_task(self, task_id: str, failure: DomainFailure, now: datetime) -> bool: ...

    async def visible_document_versions(self, document_ids: Sequence[str]) -> Mapping[str, int]: ...
