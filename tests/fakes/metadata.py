"""Transactional in-memory MetadataRepository used only by tests."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime

from rag_mvp.domain.enums import (
    DocumentStatus,
    FingerprintState,
    IndexBuildStatus,
    JobStatus,
    JobType,
    OutboxStatus,
    TaskStatus,
    TaskType,
)
from rag_mvp.domain.errors import DomainError, DomainFailure
from rag_mvp.domain.ids import new_id
from rag_mvp.domain.models import (
    Chunk,
    Dataset,
    Document,
    IndexBuild,
    IngestionFingerprint,
    Job,
    OutboxEvent,
    Task,
)
from rag_mvp.ports.metadata import (
    DeleteDocumentRequest,
    DeleteDocumentResult,
    RetryJobRequest,
    RetryJobResult,
    SubmitIngestion,
    SubmitResult,
    TaskClaim,
)


class InjectedRepositoryFailure(RuntimeError):
    pass


class FakeMetadataRepository:
    """Deterministic fake preserving compound-write and conditional-update semantics."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.datasets: dict[str, Dataset] = {}
        self.documents: dict[str, Document] = {}
        self.fingerprints: dict[tuple[str, str, str], IngestionFingerprint] = {}
        self.jobs: dict[str, Job] = {}
        self.tasks: dict[str, Task] = {}
        self.outbox: dict[str, OutboxEvent] = {}
        self.index_builds: dict[tuple[str, int], IndexBuild] = {}
        self.chunk_manifests: dict[tuple[str, int], tuple[Chunk, ...]] = {}
        self._idempotency: dict[str, SubmitResult] = {}
        self._retry_idempotency: dict[str, RetryJobResult] = {}
        self._delete_idempotency: dict[str, DeleteDocumentResult] = {}
        self.fail_next_submit = False

    async def create_dataset(self, dataset: Dataset) -> Dataset:
        async with self._lock:
            existing = self.datasets.get(dataset.id)
            if existing is not None:
                return existing
            self.datasets[dataset.id] = dataset
            return dataset

    async def get_dataset(self, dataset_id: str) -> Dataset | None:
        return self.datasets.get(dataset_id)

    async def submit_ingestion(self, command: SubmitIngestion) -> SubmitResult:
        async with self._lock:
            if command.dataset_id not in self.datasets:
                raise KeyError(command.dataset_id)
            idempotent = self._idempotency.get(command.idempotency_key)
            if idempotent is not None:
                return replace(idempotent, reused=True, staging_referenced=False)

            fingerprint_key = (
                command.dataset_id,
                command.file_sha256,
                command.config_digest,
            )
            fingerprint = self.fingerprints.get(fingerprint_key)
            if fingerprint is not None and fingerprint.state is not FingerprintState.RELEASED:
                existing_task = next(
                    task for task in self.tasks.values() if task.job_id == fingerprint.job_id
                )
                result = SubmitResult(
                    document_id=fingerprint.document_id,
                    job_id=fingerprint.job_id,
                    task_id=existing_task.id,
                    reused=True,
                    staging_referenced=False,
                )
                self._idempotency[command.idempotency_key] = result
                return result

            if self.fail_next_submit:
                self.fail_next_submit = False
                raise InjectedRepositoryFailure("submit fault injected before atomic write")

            document_id = command.target_document_id or new_id()
            job_id = new_id()
            task_id = new_id()
            event_id = new_id()
            document = Document(
                id=document_id,
                dataset_id=command.dataset_id,
                source_name=command.source_name,
                file_sha256=command.file_sha256,
                status=DocumentStatus.PENDING,
                active_version=None,
                next_index_version=2,
                lifecycle_generation=0,
                created_at=command.now,
            )
            job = Job(
                id=job_id,
                type=JobType.INGEST_DOCUMENT,
                document_id=document_id,
                config_digest=command.config_digest,
                index_version=1,
                document_generation=0,
                status=JobStatus.PENDING,
                progress=0.0,
                created_at=command.now,
            )
            task = Task(
                id=task_id,
                job_id=job_id,
                type=TaskType.INGEST_DOCUMENT,
                status=TaskStatus.PENDING,
                attempt=0,
                last_delivery_sequence=None,
                checkpoint=None,
                created_at=command.now,
            )
            event = OutboxEvent(
                id=event_id,
                task_id=task_id,
                status=OutboxStatus.WAITING_OBJECT,
                attempt=0,
                staging_key=command.staging_key,
                created_at=command.now,
            )
            fingerprint = IngestionFingerprint(
                dataset_id=command.dataset_id,
                file_sha256=command.file_sha256,
                config_digest=command.config_digest,
                document_id=document_id,
                job_id=job_id,
                state=FingerprintState.PENDING,
            )
            index_build = IndexBuild(
                document_id=document_id,
                index_version=1,
                job_id=job_id,
                status=IndexBuildStatus.BUILDING,
                created_at=command.now,
            )
            result = SubmitResult(
                document_id=document_id,
                job_id=job_id,
                task_id=task_id,
                reused=False,
                staging_referenced=True,
            )
            self.documents[document_id] = document
            self.jobs[job_id] = job
            self.tasks[task_id] = task
            self.outbox[event_id] = event
            self.fingerprints[fingerprint_key] = fingerprint
            self.index_builds[(document_id, 1)] = index_build
            self._idempotency[command.idempotency_key] = result
            return result

    async def get_job(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    async def get_task(self, task_id: str) -> Task | None:
        return self.tasks.get(task_id)

    async def get_task_for_job(self, job_id: str) -> Task | None:
        return next((task for task in self.tasks.values() if task.job_id == job_id), None)

    async def get_document(self, document_id: str) -> Document | None:
        return self.documents.get(document_id)

    async def list_waiting_outbox(self, limit: int) -> Sequence[OutboxEvent]:
        return tuple(
            event
            for event in sorted(self.outbox.values(), key=lambda item: (item.created_at, item.id))
            if event.status is OutboxStatus.WAITING_OBJECT
        )[:limit]

    async def mark_object_ready(self, event_id: str, object_key: str, now: datetime) -> bool:
        del now
        async with self._lock:
            event = self.outbox.get(event_id)
            if event is None or event.status is not OutboxStatus.WAITING_OBJECT:
                return False
            task = self.tasks[event.task_id]
            job = self.jobs[task.job_id]
            document = self.documents[job.document_id]
            if document.status is DocumentStatus.DELETED:
                return False
            self.documents[document.id] = replace(document, object_key=object_key)
            self.outbox[event_id] = replace(event, status=OutboxStatus.READY_TO_PUBLISH)
            return True

    async def list_ready_outbox(self, limit: int) -> Sequence[OutboxEvent]:
        return tuple(
            event
            for event in sorted(self.outbox.values(), key=lambda item: (item.created_at, item.id))
            if event.status is OutboxStatus.READY_TO_PUBLISH
        )[:limit]

    async def mark_outbox_published(self, event_id: str, now: datetime) -> bool:
        async with self._lock:
            event = self.outbox.get(event_id)
            if event is None or event.status is not OutboxStatus.READY_TO_PUBLISH:
                return False
            self.outbox[event_id] = replace(event, status=OutboxStatus.PUBLISHED, published_at=now)
            return True

    async def claim_task(
        self, task_id: str, delivery_sequence: int, now: datetime
    ) -> TaskClaim | None:
        del now
        async with self._lock:
            task = self.tasks.get(task_id)
            if task is None or task.status not in {TaskStatus.PENDING, TaskStatus.RUNNING}:
                return None
            if (
                task.last_delivery_sequence is not None
                and delivery_sequence <= task.last_delivery_sequence
            ):
                return None
            job = self.jobs[task.job_id]
            document = self.documents[job.document_id]
            if job.cancel_requested_at is not None or (
                document.status is DocumentStatus.DELETED and task.type is TaskType.INGEST_DOCUMENT
            ):
                return None
            claimed_task = replace(
                task,
                status=TaskStatus.RUNNING,
                attempt=task.attempt + 1,
                last_delivery_sequence=delivery_sequence,
            )
            claimed_job = replace(job, status=JobStatus.RUNNING, progress=max(job.progress, 0.01))
            self.tasks[task_id] = claimed_task
            self.jobs[job.id] = claimed_job
            for key, fingerprint in self.fingerprints.items():
                if fingerprint.job_id == job.id:
                    self.fingerprints[key] = replace(fingerprint, state=FingerprintState.RUNNING)
                    break
            return TaskClaim(task=claimed_task, job=claimed_job, document=document)

    async def complete_ingestion(
        self, task_id: str, chunks: Sequence[Chunk], now: datetime
    ) -> bool:
        del now
        async with self._lock:
            task = self.tasks.get(task_id)
            if task is None or task.status is not TaskStatus.RUNNING:
                return False
            job = self.jobs[task.job_id]
            document = self.documents[job.document_id]
            if (
                job.cancel_requested_at is not None
                or document.status is DocumentStatus.DELETED
                or document.lifecycle_generation != job.document_generation
            ):
                return False
            self.chunk_manifests[(document.id, job.index_version)] = tuple(chunks)
            self.index_builds[(document.id, job.index_version)] = replace(
                self.index_builds[(document.id, job.index_version)],
                status=IndexBuildStatus.ACTIVE,
            )
            self.documents[document.id] = replace(
                document, status=DocumentStatus.READY, active_version=job.index_version
            )
            self.tasks[task.id] = replace(task, status=TaskStatus.SUCCEEDED, checkpoint="complete")
            self.jobs[job.id] = replace(job, status=JobStatus.SUCCEEDED, progress=1.0)
            for key, fingerprint in self.fingerprints.items():
                if fingerprint.job_id == job.id:
                    self.fingerprints[key] = replace(fingerprint, state=FingerprintState.SUCCEEDED)
                    break
            return True

    async def fail_task(self, task_id: str, failure: DomainFailure, now: datetime) -> bool:
        del now
        async with self._lock:
            task = self.tasks.get(task_id)
            if task is None or task.status not in {TaskStatus.PENDING, TaskStatus.RUNNING}:
                return False
            job = self.jobs[task.job_id]
            document = self.documents[job.document_id]
            self.tasks[task_id] = replace(task, status=TaskStatus.FAILED, error=failure)
            self.jobs[job.id] = replace(
                job,
                status=JobStatus.FAILED,
                error=failure,
                retryable=failure.retryable,
            )
            if document.active_version is None:
                self.documents[document.id] = replace(document, status=DocumentStatus.FAILED)
            for key, fingerprint in self.fingerprints.items():
                if fingerprint.job_id == job.id:
                    state = (
                        FingerprintState.FAILED_RETRYABLE
                        if failure.retryable and document.object_key is not None
                        else FingerprintState.RELEASED
                    )
                    self.fingerprints[key] = replace(fingerprint, state=state)
                    break
            return True

    async def retry_job(self, request: RetryJobRequest) -> RetryJobResult:
        async with self._lock:
            repeated = self._retry_idempotency.get(request.idempotency_key)
            if repeated is not None:
                return replace(repeated, reused=True)
            original = self.jobs.get(request.job_id)
            if original is None:
                raise DomainError(DomainFailure("JOB_NOT_FOUND", "job does not exist"))
            if original.status is not JobStatus.FAILED:
                raise DomainError(
                    DomainFailure("JOB_NOT_FAILED", "only failed jobs can be retried")
                )
            if not original.retryable:
                raise DomainError(
                    DomainFailure("JOB_NOT_RETRYABLE", "job failure is not retryable")
                )
            document = self.documents[original.document_id]
            if document.object_key is None:
                raise DomainError(
                    DomainFailure(
                        "RETRY_OBJECT_MISSING", "retry requires a finalized source object"
                    )
                )
            active_child = next(
                (
                    job
                    for job in self.jobs.values()
                    if job.retry_of_job_id == original.id
                    and job.status in {JobStatus.PENDING, JobStatus.RUNNING}
                ),
                None,
            )
            if active_child is not None:
                active_task = next(
                    task for task in self.tasks.values() if task.job_id == active_child.id
                )
                result = RetryJobResult(active_child.id, active_task.id, reused=True)
                self._retry_idempotency[request.idempotency_key] = result
                return result
            if original.retry_count >= request.max_user_retries:
                raise DomainError(
                    DomainFailure("MAX_USER_RETRIES_EXCEEDED", "job reached its user retry limit")
                )

            job_id = new_id()
            task_id = new_id()
            retry_count = original.retry_count + 1
            child = Job(
                id=job_id,
                type=original.type,
                document_id=original.document_id,
                config_digest=original.config_digest,
                index_version=original.index_version,
                document_generation=original.document_generation,
                status=JobStatus.PENDING,
                progress=0.0,
                created_at=request.now,
                retry_count=retry_count,
                retry_of_job_id=original.id,
            )
            task = Task(
                id=task_id,
                job_id=job_id,
                type=TaskType.INGEST_DOCUMENT,
                status=TaskStatus.PENDING,
                attempt=0,
                last_delivery_sequence=None,
                checkpoint=None,
                created_at=request.now,
            )
            event = OutboxEvent(
                id=new_id(),
                task_id=task_id,
                status=OutboxStatus.READY_TO_PUBLISH,
                attempt=0,
                staging_key=None,
                created_at=request.now,
            )
            self.jobs[original.id] = replace(original, retry_count=retry_count)
            self.jobs[job_id] = child
            self.tasks[task_id] = task
            self.outbox[event.id] = event
            index_key = (document.id, child.index_version)
            index_build = self.index_builds.get(index_key)
            if index_build is not None:
                self.index_builds[index_key] = replace(
                    index_build, job_id=child.id, status=IndexBuildStatus.BUILDING
                )
            for key, fingerprint in self.fingerprints.items():
                if fingerprint.document_id == document.id:
                    self.fingerprints[key] = replace(
                        fingerprint,
                        job_id=child.id,
                        state=FingerprintState.PENDING,
                    )
                    break
            result = RetryJobResult(job_id, task_id, reused=False)
            self._retry_idempotency[request.idempotency_key] = result
            return result

    async def delete_document(self, request: DeleteDocumentRequest) -> DeleteDocumentResult:
        async with self._lock:
            repeated = self._delete_idempotency.get(request.idempotency_key)
            if repeated is not None:
                return replace(repeated, reused=True)
            document = self.documents.get(request.document_id)
            if document is None:
                raise DomainError(DomainFailure("DOCUMENT_NOT_FOUND", "document does not exist"))
            if document.status is DocumentStatus.DELETED:
                raise DomainError(
                    DomainFailure("DOCUMENT_ALREADY_DELETED", "document is already deleted")
                )

            deleted_document = replace(
                document,
                status=DocumentStatus.DELETED,
                lifecycle_generation=document.lifecycle_generation + 1,
            )
            self.documents[document.id] = deleted_document
            for key, fingerprint in self.fingerprints.items():
                if fingerprint.document_id == document.id:
                    self.fingerprints[key] = replace(fingerprint, state=FingerprintState.RELEASED)

            cancelled_failure = DomainFailure(
                "DOCUMENT_DELETED", "document was deleted before ingestion completed"
            )
            cancelled_task_ids: set[str] = set()
            for task_id, task in tuple(self.tasks.items()):
                job = self.jobs[task.job_id]
                if (
                    job.document_id == document.id
                    and task.type is TaskType.INGEST_DOCUMENT
                    and task.status in {TaskStatus.PENDING, TaskStatus.RUNNING}
                ):
                    self.tasks[task_id] = replace(
                        task, status=TaskStatus.CANCELLED, error=cancelled_failure
                    )
                    self.jobs[job.id] = replace(
                        job, status=JobStatus.CANCELLED, error=cancelled_failure
                    )
                    cancelled_task_ids.add(task_id)
            for event_id, event in tuple(self.outbox.items()):
                if event.task_id in cancelled_task_ids and event.status in {
                    OutboxStatus.WAITING_OBJECT,
                    OutboxStatus.READY_TO_PUBLISH,
                }:
                    self.outbox[event_id] = replace(event, status=OutboxStatus.CANCELLED)

            job_id = new_id()
            task_id = new_id()
            job = Job(
                id=job_id,
                type=JobType.DELETE_DOCUMENT,
                document_id=document.id,
                config_digest="0" * 64,
                index_version=document.active_version or 1,
                document_generation=deleted_document.lifecycle_generation,
                status=JobStatus.PENDING,
                progress=0.0,
                created_at=request.now,
            )
            task = Task(
                id=task_id,
                job_id=job_id,
                type=TaskType.CLEANUP_DOCUMENT,
                status=TaskStatus.PENDING,
                attempt=0,
                last_delivery_sequence=None,
                checkpoint=None,
                created_at=request.now,
            )
            event = OutboxEvent(
                id=new_id(),
                task_id=task_id,
                status=OutboxStatus.READY_TO_PUBLISH,
                attempt=0,
                staging_key=None,
                created_at=request.now,
            )
            self.jobs[job_id] = job
            self.tasks[task_id] = task
            self.outbox[event.id] = event
            result = DeleteDocumentResult(document.id, job_id, task_id, reused=False)
            self._delete_idempotency[request.idempotency_key] = result
            return result

    async def complete_cleanup(self, task_id: str, now: datetime) -> bool:
        del now
        async with self._lock:
            task = self.tasks.get(task_id)
            if (
                task is None
                or task.status is not TaskStatus.RUNNING
                or task.type is not TaskType.CLEANUP_DOCUMENT
            ):
                return False
            job = self.jobs[task.job_id]
            document = self.documents[job.document_id]
            if (
                document.status is not DocumentStatus.DELETED
                or document.lifecycle_generation != job.document_generation
            ):
                return False
            self.chunk_manifests = {
                key: chunks for key, chunks in self.chunk_manifests.items() if key[0] != document.id
            }
            self.index_builds = {
                key: build for key, build in self.index_builds.items() if key[0] != document.id
            }
            self.tasks[task.id] = replace(
                task, status=TaskStatus.SUCCEEDED, checkpoint="cleanup_complete"
            )
            self.jobs[job.id] = replace(job, status=JobStatus.SUCCEEDED, progress=1.0)
            return True

    async def visible_document_versions(self, document_ids: Sequence[str]) -> Mapping[str, int]:
        return {
            document_id: document.active_version
            for document_id in document_ids
            if (document := self.documents.get(document_id)) is not None
            and document.status is DocumentStatus.READY
            and document.active_version is not None
        }

    def counts(self) -> dict[str, int]:
        return {
            "documents": len(self.documents),
            "fingerprints": len(self.fingerprints),
            "jobs": len(self.jobs),
            "tasks": len(self.tasks),
            "outbox": len(self.outbox),
            "index_builds": len(self.index_builds),
        }
