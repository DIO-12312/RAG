"""MySQL implementation of authoritative metadata transactions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import cast

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rag_mvp.adapters.metadata.mappers import (
    dataset_from_table,
    document_from_table,
    failure_to_json,
    job_from_table,
    outbox_from_table,
    task_from_table,
)
from rag_mvp.adapters.metadata.tables import (
    ChunkManifestTable,
    DatasetTable,
    DocumentTable,
    IdempotencyRecordTable,
    IndexBuildTable,
    IngestionFingerprintTable,
    JobTable,
    OutboxEventTable,
    TaskTable,
)
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
from rag_mvp.domain.models import Chunk, Dataset, Document, Job, OutboxEvent, Task
from rag_mvp.ports.metadata import SubmitIngestion, SubmitResult, TaskClaim

SUBMIT_OPERATION = "SUBMIT_INGESTION"
MYSQL_DUPLICATE_KEY = 1062


@dataclass(frozen=True, slots=True)
class _LockedTaskAggregate:
    document: DocumentTable
    job: JobTable
    task: TaskTable


@dataclass(frozen=True, slots=True)
class _LockedEventAggregate:
    document: DocumentTable
    job: JobTable
    task: TaskTable
    event: OutboxEventTable


class MySQLMetadataRepository:
    """Persist metadata with short READ COMMITTED transactions and explicit locks."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        default_tenant_id: str,
    ) -> None:
        if not default_tenant_id.strip():
            raise ValueError("default_tenant_id must not be empty")
        self._session_factory = session_factory
        self._default_tenant_id = default_tenant_id

    async def create_dataset(self, dataset: Dataset) -> Dataset:
        if dataset.tenant_id != self._default_tenant_id:
            raise DomainError(
                DomainFailure("TENANT_MISMATCH", "dataset tenant does not match this service")
            )
        async with self._session_factory() as session, session.begin():
            existing = await session.get(DatasetTable, dataset.id)
            if existing is not None:
                if existing.tenant_id != self._default_tenant_id:
                    raise DomainError(
                        DomainFailure("TENANT_MISMATCH", "dataset belongs to another tenant")
                    )
                return dataset_from_table(existing)
            row = DatasetTable(
                id=dataset.id,
                tenant_id=dataset.tenant_id,
                name=dataset.name,
                embedding_model=dataset.embedding_model,
                embedding_dimension=dataset.embedding_dimension,
                search_schema_version=dataset.search_schema_version,
                created_at=dataset.created_at,
                updated_at=dataset.created_at,
            )
            session.add(row)
        return dataset

    async def get_dataset(self, dataset_id: str) -> Dataset | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(DatasetTable).where(
                    DatasetTable.id == dataset_id,
                    DatasetTable.tenant_id == self._default_tenant_id,
                )
            )
            return dataset_from_table(row) if row is not None else None

    async def submit_ingestion(self, command: SubmitIngestion) -> SubmitResult:
        request_digest = self._submission_digest(command)
        try:
            return await self._submit_transaction(command, request_digest)
        except IntegrityError as conflict:
            if not self._is_duplicate_key(conflict):
                raise
            return await self._resolve_submit_conflict(command, request_digest, conflict)

    async def _submit_transaction(
        self,
        command: SubmitIngestion,
        request_digest: str,
    ) -> SubmitResult:
        async with self._session_factory() as session, session.begin():
            idempotent = await self._locked_idempotency(session, command.idempotency_key)
            if idempotent is not None:
                return self._recorded_submit_result(idempotent, request_digest)

            dataset = await session.scalar(
                select(DatasetTable).where(
                    DatasetTable.id == command.dataset_id,
                    DatasetTable.tenant_id == self._default_tenant_id,
                )
            )
            if dataset is None:
                raise DomainError(DomainFailure("DATASET_NOT_FOUND", "dataset does not exist"))

            fingerprint: IngestionFingerprintTable | None = None
            if command.target_document_id is None:
                fingerprint = await self._locked_fingerprint(session, command)
                if fingerprint is not None and fingerprint.state != FingerprintState.RELEASED:
                    return await self._reuse_fingerprint(
                        session,
                        command,
                        fingerprint,
                        request_digest,
                    )

            document, index_version = await self._prepare_document(session, command)
            job_id = new_id()
            task_id = new_id()
            job = JobTable(
                id=job_id,
                type=JobType.INGEST_DOCUMENT,
                document_id=document.id,
                config_digest=command.config_digest,
                index_version=index_version,
                document_generation=document.lifecycle_generation,
                status=JobStatus.PENDING,
                progress=Decimal("0"),
                error=None,
                retryable=False,
                retry_count=0,
                cancel_requested_at=None,
                retry_of_job_id=None,
                active_retry_parent_id=None,
                is_system=False,
                created_at=command.now,
                updated_at=command.now,
            )
            session.add(job)
            await session.flush()

            if command.target_document_id is None:
                if fingerprint is None:
                    fingerprint = IngestionFingerprintTable(
                        dataset_id=command.dataset_id,
                        file_sha256=command.file_sha256,
                        config_digest=command.config_digest,
                        document_id=document.id,
                        job_id=job_id,
                        state=FingerprintState.PENDING,
                        created_at=command.now,
                        updated_at=command.now,
                    )
                    session.add(fingerprint)
                else:
                    fingerprint.document_id = document.id
                    fingerprint.job_id = job_id
                    fingerprint.state = FingerprintState.PENDING
                    fingerprint.updated_at = command.now
                await session.flush()

            session.add(
                TaskTable(
                    id=task_id,
                    job_id=job_id,
                    type=TaskType.INGEST_DOCUMENT,
                    status=TaskStatus.PENDING,
                    attempt=0,
                    last_delivery_sequence=None,
                    checkpoint=None,
                    error=None,
                    created_at=command.now,
                    updated_at=command.now,
                )
            )
            await session.flush()
            session.add_all(
                (
                    OutboxEventTable(
                        id=new_id(),
                        task_id=task_id,
                        status=OutboxStatus.WAITING_OBJECT,
                        attempt=0,
                        staging_key=command.staging_key,
                        published_at=None,
                        created_at=command.now,
                        updated_at=command.now,
                    ),
                    IndexBuildTable(
                        document_id=document.id,
                        index_version=index_version,
                        job_id=job_id,
                        status=IndexBuildStatus.BUILDING,
                        created_at=command.now,
                        updated_at=command.now,
                    ),
                )
            )
            result = SubmitResult(
                document_id=document.id,
                job_id=job_id,
                task_id=task_id,
                reused=False,
                staging_referenced=True,
            )
            session.add(
                self._new_idempotency_record(
                    command.idempotency_key,
                    request_digest,
                    result,
                    command.now,
                )
            )
        return result

    async def _prepare_document(
        self,
        session: AsyncSession,
        command: SubmitIngestion,
    ) -> tuple[DocumentTable, int]:
        if command.target_document_id is None:
            document = DocumentTable(
                id=new_id(),
                dataset_id=command.dataset_id,
                source_name=command.source_name,
                file_sha256=command.file_sha256,
                status=DocumentStatus.PENDING,
                active_version=None,
                next_index_version=2,
                lifecycle_generation=0,
                object_key=None,
                created_at=command.now,
                updated_at=command.now,
            )
            session.add(document)
            await session.flush()
            return document, 1

        existing_document = await session.scalar(
            select(DocumentTable)
            .where(DocumentTable.id == command.target_document_id)
            .with_for_update()
        )
        if existing_document is None:
            raise DomainError(DomainFailure("DOCUMENT_NOT_FOUND", "target document does not exist"))
        if existing_document.dataset_id != command.dataset_id:
            raise DomainError(
                DomainFailure(
                    "DOCUMENT_DATASET_MISMATCH",
                    "target document belongs to another dataset",
                )
            )
        if existing_document.status == DocumentStatus.DELETED:
            raise DomainError(
                DomainFailure("DOCUMENT_ALREADY_DELETED", "target document is deleted")
            )
        index_version = existing_document.next_index_version
        existing_document.source_name = command.source_name
        existing_document.file_sha256 = command.file_sha256
        existing_document.next_index_version = index_version + 1
        existing_document.updated_at = command.now
        await session.flush()
        return existing_document, index_version

    async def _resolve_submit_conflict(
        self,
        command: SubmitIngestion,
        request_digest: str,
        conflict: IntegrityError,
    ) -> SubmitResult:
        async with self._session_factory() as session, session.begin():
            idempotent = await self._locked_idempotency(session, command.idempotency_key)
            if idempotent is not None:
                return self._recorded_submit_result(idempotent, request_digest)
            if command.target_document_id is not None:
                raise conflict
            fingerprint = await self._locked_fingerprint(session, command)
            if fingerprint is None or fingerprint.state == FingerprintState.RELEASED:
                raise conflict
            return await self._reuse_fingerprint(
                session,
                command,
                fingerprint,
                request_digest,
            )

    async def _reuse_fingerprint(
        self,
        session: AsyncSession,
        command: SubmitIngestion,
        fingerprint: IngestionFingerprintTable,
        request_digest: str,
    ) -> SubmitResult:
        task = await session.scalar(
            select(TaskTable)
            .where(TaskTable.job_id == fingerprint.job_id)
            .order_by(TaskTable.created_at, TaskTable.id)
            .limit(1)
        )
        if task is None:
            raise RuntimeError("canonical fingerprint has no task")
        result = SubmitResult(
            document_id=fingerprint.document_id,
            job_id=fingerprint.job_id,
            task_id=task.id,
            reused=True,
            staging_referenced=False,
        )
        session.add(
            self._new_idempotency_record(
                command.idempotency_key,
                request_digest,
                result,
                command.now,
            )
        )
        await session.flush()
        return result

    async def _locked_idempotency(
        self,
        session: AsyncSession,
        idempotency_key: str,
    ) -> IdempotencyRecordTable | None:
        return cast(
            IdempotencyRecordTable | None,
            await session.scalar(
                select(IdempotencyRecordTable)
                .where(
                    IdempotencyRecordTable.operation_type == SUBMIT_OPERATION,
                    IdempotencyRecordTable.idempotency_key == idempotency_key,
                )
                .with_for_update()
            ),
        )

    @staticmethod
    async def _locked_fingerprint(
        session: AsyncSession,
        command: SubmitIngestion,
    ) -> IngestionFingerprintTable | None:
        return cast(
            IngestionFingerprintTable | None,
            await session.scalar(
                select(IngestionFingerprintTable)
                .where(
                    IngestionFingerprintTable.dataset_id == command.dataset_id,
                    IngestionFingerprintTable.file_sha256 == command.file_sha256,
                    IngestionFingerprintTable.config_digest == command.config_digest,
                )
                .with_for_update()
            ),
        )

    @staticmethod
    def _new_idempotency_record(
        idempotency_key: str,
        request_digest: str,
        result: SubmitResult,
        now: datetime,
    ) -> IdempotencyRecordTable:
        return IdempotencyRecordTable(
            operation_type=SUBMIT_OPERATION,
            idempotency_key=idempotency_key,
            request_digest=request_digest,
            result_json={
                "document_id": result.document_id,
                "job_id": result.job_id,
                "task_id": result.task_id,
            },
            created_at=now,
        )

    @staticmethod
    def _recorded_submit_result(
        record: IdempotencyRecordTable,
        request_digest: str,
    ) -> SubmitResult:
        if record.request_digest != request_digest:
            raise DomainError(
                DomainFailure(
                    "IDEMPOTENCY_KEY_REUSED",
                    "idempotency key was already used for another submission",
                )
            )
        value = record.result_json
        return SubmitResult(
            document_id=str(value["document_id"]),
            job_id=str(value["job_id"]),
            task_id=str(value["task_id"]),
            reused=True,
            staging_referenced=False,
        )

    @staticmethod
    def _submission_digest(command: SubmitIngestion) -> str:
        payload = json.dumps(
            {
                "dataset_id": command.dataset_id,
                "source_name": command.source_name,
                "file_sha256": command.file_sha256,
                "config_digest": command.config_digest,
                "target_document_id": command.target_document_id,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _is_duplicate_key(error: IntegrityError) -> bool:
        arguments = getattr(error.orig, "args", ())
        return bool(arguments and arguments[0] == MYSQL_DUPLICATE_KEY)

    async def get_job(self, job_id: str) -> Job | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(JobTable)
                .join(DocumentTable, DocumentTable.id == JobTable.document_id)
                .join(DatasetTable, DatasetTable.id == DocumentTable.dataset_id)
                .where(
                    JobTable.id == job_id,
                    DatasetTable.tenant_id == self._default_tenant_id,
                )
            )
            return job_from_table(row) if row is not None else None

    async def get_task(self, task_id: str) -> Task | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(TaskTable)
                .join(JobTable, JobTable.id == TaskTable.job_id)
                .join(DocumentTable, DocumentTable.id == JobTable.document_id)
                .join(DatasetTable, DatasetTable.id == DocumentTable.dataset_id)
                .where(
                    TaskTable.id == task_id,
                    DatasetTable.tenant_id == self._default_tenant_id,
                )
            )
            return task_from_table(row) if row is not None else None

    async def get_task_for_job(self, job_id: str) -> Task | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(TaskTable)
                .join(JobTable, JobTable.id == TaskTable.job_id)
                .join(DocumentTable, DocumentTable.id == JobTable.document_id)
                .join(DatasetTable, DatasetTable.id == DocumentTable.dataset_id)
                .where(
                    TaskTable.job_id == job_id,
                    DatasetTable.tenant_id == self._default_tenant_id,
                )
                .order_by(TaskTable.created_at, TaskTable.id)
                .limit(1)
            )
            return task_from_table(row) if row is not None else None

    async def get_document(self, document_id: str) -> Document | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(DocumentTable)
                .join(DatasetTable, DatasetTable.id == DocumentTable.dataset_id)
                .where(
                    DocumentTable.id == document_id,
                    DatasetTable.tenant_id == self._default_tenant_id,
                )
            )
            return document_from_table(row) if row is not None else None

    async def list_waiting_outbox(self, limit: int) -> Sequence[OutboxEvent]:
        return await self._list_outbox(OutboxStatus.WAITING_OBJECT, limit)

    async def list_ready_outbox(self, limit: int) -> Sequence[OutboxEvent]:
        return await self._list_outbox(OutboxStatus.READY_TO_PUBLISH, limit)

    async def _list_outbox(
        self,
        status: OutboxStatus,
        limit: int,
    ) -> Sequence[OutboxEvent]:
        if limit < 1:
            return ()
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(OutboxEventTable)
                .join(TaskTable, TaskTable.id == OutboxEventTable.task_id)
                .join(JobTable, JobTable.id == TaskTable.job_id)
                .join(DocumentTable, DocumentTable.id == JobTable.document_id)
                .join(DatasetTable, DatasetTable.id == DocumentTable.dataset_id)
                .where(
                    OutboxEventTable.status == status,
                    DatasetTable.tenant_id == self._default_tenant_id,
                )
                .order_by(OutboxEventTable.created_at, OutboxEventTable.id)
                .limit(limit)
            )
            return tuple(outbox_from_table(row) for row in rows)

    async def waiting_staging_keys(self) -> Sequence[str]:
        async with self._session_factory() as session:
            keys = await session.scalars(
                select(OutboxEventTable.staging_key)
                .join(TaskTable, TaskTable.id == OutboxEventTable.task_id)
                .join(JobTable, JobTable.id == TaskTable.job_id)
                .join(DocumentTable, DocumentTable.id == JobTable.document_id)
                .join(DatasetTable, DatasetTable.id == DocumentTable.dataset_id)
                .where(
                    OutboxEventTable.status == OutboxStatus.WAITING_OBJECT,
                    OutboxEventTable.staging_key.is_not(None),
                    DatasetTable.tenant_id == self._default_tenant_id,
                )
                .order_by(OutboxEventTable.created_at, OutboxEventTable.id)
            )
            return tuple(cast(str, key) for key in keys)

    async def mark_object_ready(self, event_id: str, object_key: str, now: datetime) -> bool:
        if not object_key.strip():
            raise ValueError("object_key must not be empty")
        async with self._session_factory() as session, session.begin():
            aggregate = await self._lock_event_aggregate(session, event_id)
            if aggregate is None:
                return False
            if aggregate.event.status != OutboxStatus.WAITING_OBJECT:
                return False
            if aggregate.task.status != TaskStatus.PENDING:
                return False
            if aggregate.job.status != JobStatus.PENDING:
                return False
            if aggregate.job.cancel_requested_at is not None:
                return False
            if aggregate.document.status == DocumentStatus.DELETED:
                return False
            if aggregate.document.lifecycle_generation != aggregate.job.document_generation:
                return False
            aggregate.document.object_key = object_key
            aggregate.document.updated_at = now
            aggregate.event.status = OutboxStatus.READY_TO_PUBLISH
            aggregate.event.updated_at = now
            return True

    async def record_finalization_failure(
        self,
        event_id: str,
        max_attempts: int,
        now: datetime,
    ) -> bool:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        async with self._session_factory() as session, session.begin():
            aggregate = await self._lock_event_aggregate(session, event_id)
            if aggregate is None or aggregate.event.status != OutboxStatus.WAITING_OBJECT:
                return False
            aggregate.event.attempt += 1
            aggregate.event.updated_at = now
            if aggregate.event.attempt < max_attempts:
                return False

            failure = DomainFailure(
                "OBJECT_FINALIZATION_FAILED",
                "source object could not be finalized",
                retryable=False,
            )
            encoded_failure = failure_to_json(failure)
            aggregate.event.status = OutboxStatus.CANCELLED
            aggregate.task.status = TaskStatus.FAILED
            aggregate.task.error = encoded_failure
            aggregate.task.updated_at = now
            aggregate.job.status = JobStatus.FAILED
            aggregate.job.error = encoded_failure
            aggregate.job.retryable = False
            aggregate.job.updated_at = now
            if (
                aggregate.document.active_version is None
                and aggregate.document.status != DocumentStatus.DELETED
            ):
                aggregate.document.status = DocumentStatus.FAILED
                aggregate.document.updated_at = now
            await session.execute(
                update(IngestionFingerprintTable)
                .where(IngestionFingerprintTable.job_id == aggregate.job.id)
                .values(state=FingerprintState.RELEASED, updated_at=now)
            )
            await session.execute(
                update(IndexBuildTable)
                .where(
                    IndexBuildTable.document_id == aggregate.document.id,
                    IndexBuildTable.index_version == aggregate.job.index_version,
                    IndexBuildTable.status == IndexBuildStatus.BUILDING,
                )
                .values(status=IndexBuildStatus.ABANDONED, updated_at=now)
            )
            return True

    async def mark_outbox_published(self, event_id: str, now: datetime) -> bool:
        async with self._session_factory() as session, session.begin():
            aggregate = await self._lock_event_aggregate(session, event_id)
            if aggregate is None or aggregate.event.status != OutboxStatus.READY_TO_PUBLISH:
                return False
            aggregate.event.status = OutboxStatus.PUBLISHED
            aggregate.event.published_at = now
            aggregate.event.updated_at = now
            return True

    async def claim_task(
        self,
        task_id: str,
        delivery_sequence: int,
        now: datetime,
    ) -> TaskClaim | None:
        if delivery_sequence < 1:
            raise ValueError("delivery_sequence must be at least 1")
        async with self._session_factory() as session, session.begin():
            aggregate = await self._lock_task_aggregate(session, task_id)
            if aggregate is None:
                return None
            if aggregate.task.status not in {TaskStatus.PENDING, TaskStatus.RUNNING}:
                return None
            if aggregate.job.status not in {JobStatus.PENDING, JobStatus.RUNNING}:
                return None
            if (
                aggregate.task.last_delivery_sequence is not None
                and delivery_sequence <= aggregate.task.last_delivery_sequence
            ):
                return None
            if aggregate.job.cancel_requested_at is not None:
                return None
            if aggregate.document.lifecycle_generation != aggregate.job.document_generation:
                return None
            if (
                aggregate.task.type == TaskType.INGEST_DOCUMENT
                and aggregate.document.status == DocumentStatus.DELETED
            ):
                return None

            aggregate.task.status = TaskStatus.RUNNING
            aggregate.task.attempt += 1
            aggregate.task.last_delivery_sequence = delivery_sequence
            aggregate.task.updated_at = now
            aggregate.job.status = JobStatus.RUNNING
            aggregate.job.progress = max(aggregate.job.progress, Decimal("0.01"))
            aggregate.job.updated_at = now
            await session.execute(
                update(IngestionFingerprintTable)
                .where(IngestionFingerprintTable.job_id == aggregate.job.id)
                .values(state=FingerprintState.RUNNING, updated_at=now)
            )
            await session.flush()
            return TaskClaim(
                task=task_from_table(aggregate.task),
                job=job_from_table(aggregate.job),
                document=document_from_table(aggregate.document),
            )

    async def complete_ingestion(
        self,
        task_id: str,
        chunks: Sequence[Chunk],
        now: datetime,
    ) -> bool:
        async with self._session_factory() as session, session.begin():
            aggregate = await self._lock_task_aggregate(session, task_id)
            if aggregate is None or aggregate.task.status != TaskStatus.RUNNING:
                return False
            if aggregate.task.type != TaskType.INGEST_DOCUMENT:
                return False
            if aggregate.job.status != JobStatus.RUNNING:
                return False
            if aggregate.job.cancel_requested_at is not None:
                return False
            if aggregate.document.status == DocumentStatus.DELETED:
                return False
            if aggregate.document.lifecycle_generation != aggregate.job.document_generation:
                return False
            if any(
                chunk.document_id != aggregate.document.id
                or chunk.index_version != aggregate.job.index_version
                for chunk in chunks
            ):
                raise ValueError("chunk document and index version must match the claimed task")

            index_build = cast(
                IndexBuildTable | None,
                await session.scalar(
                    select(IndexBuildTable)
                    .where(
                        IndexBuildTable.document_id == aggregate.document.id,
                        IndexBuildTable.index_version == aggregate.job.index_version,
                    )
                    .with_for_update()
                ),
            )
            if index_build is None or index_build.status != IndexBuildStatus.BUILDING:
                return False

            session.add_all(
                tuple(
                    ChunkManifestTable(
                        document_id=chunk.document_id,
                        index_version=chunk.index_version,
                        chunk_id=chunk.id,
                        ordinal=chunk.ordinal,
                        content_sha256=chunk.content_sha256,
                        source_name=chunk.source_name,
                        locator={
                            "page_number": chunk.locator.page_number,
                            "start_line": chunk.locator.start_line,
                            "end_line": chunk.locator.end_line,
                            "symbol": chunk.locator.symbol,
                            "language": chunk.locator.language,
                            "metadata": dict(chunk.locator.metadata),
                        },
                        metadata_json=dict(chunk.metadata),
                        created_at=now,
                    )
                    for chunk in chunks
                )
            )
            index_build.status = IndexBuildStatus.ACTIVE
            index_build.updated_at = now
            aggregate.document.status = DocumentStatus.READY
            aggregate.document.active_version = aggregate.job.index_version
            aggregate.document.updated_at = now
            aggregate.task.status = TaskStatus.SUCCEEDED
            aggregate.task.checkpoint = "complete"
            aggregate.task.updated_at = now
            aggregate.job.status = JobStatus.SUCCEEDED
            aggregate.job.progress = Decimal("1")
            aggregate.job.updated_at = now
            await session.execute(
                update(IngestionFingerprintTable)
                .where(IngestionFingerprintTable.job_id == aggregate.job.id)
                .values(state=FingerprintState.SUCCEEDED, updated_at=now)
            )
            return True

    async def fail_task(self, task_id: str, failure: DomainFailure, now: datetime) -> bool:
        async with self._session_factory() as session, session.begin():
            aggregate = await self._lock_task_aggregate(session, task_id)
            if aggregate is None:
                return False
            if aggregate.task.status not in {TaskStatus.PENDING, TaskStatus.RUNNING}:
                return False
            if aggregate.job.status not in {JobStatus.PENDING, JobStatus.RUNNING}:
                return False

            encoded_failure = failure_to_json(failure)
            aggregate.task.status = TaskStatus.FAILED
            aggregate.task.error = encoded_failure
            aggregate.task.updated_at = now
            aggregate.job.status = JobStatus.FAILED
            aggregate.job.error = encoded_failure
            aggregate.job.retryable = failure.retryable
            aggregate.job.updated_at = now
            if (
                aggregate.document.active_version is None
                and aggregate.document.status != DocumentStatus.DELETED
            ):
                aggregate.document.status = DocumentStatus.FAILED
                aggregate.document.updated_at = now
            fingerprint_state = (
                FingerprintState.FAILED_RETRYABLE
                if failure.retryable and aggregate.document.object_key is not None
                else FingerprintState.RELEASED
            )
            await session.execute(
                update(IngestionFingerprintTable)
                .where(IngestionFingerprintTable.job_id == aggregate.job.id)
                .values(state=fingerprint_state, updated_at=now)
            )
            await session.execute(
                update(IndexBuildTable)
                .where(
                    IndexBuildTable.document_id == aggregate.document.id,
                    IndexBuildTable.index_version == aggregate.job.index_version,
                    IndexBuildTable.status == IndexBuildStatus.BUILDING,
                )
                .values(status=IndexBuildStatus.ABANDONED, updated_at=now)
            )
            return True

    async def visible_document_versions(self, document_ids: Sequence[str]) -> Mapping[str, int]:
        if not document_ids:
            return {}
        async with self._session_factory() as session:
            rows = await session.execute(
                select(DocumentTable.id, DocumentTable.active_version)
                .join(DatasetTable, DatasetTable.id == DocumentTable.dataset_id)
                .where(
                    DocumentTable.id.in_(tuple(document_ids)),
                    DocumentTable.status == DocumentStatus.READY,
                    DocumentTable.active_version.is_not(None),
                    DatasetTable.tenant_id == self._default_tenant_id,
                )
            )
            return {document_id: cast(int, version) for document_id, version in rows}

    async def _lock_task_aggregate(
        self,
        session: AsyncSession,
        task_id: str,
    ) -> _LockedTaskAggregate | None:
        identifiers = (
            await session.execute(
                select(TaskTable.job_id, JobTable.document_id)
                .join(JobTable, JobTable.id == TaskTable.job_id)
                .join(DocumentTable, DocumentTable.id == JobTable.document_id)
                .join(DatasetTable, DatasetTable.id == DocumentTable.dataset_id)
                .where(
                    TaskTable.id == task_id,
                    DatasetTable.tenant_id == self._default_tenant_id,
                )
            )
        ).one_or_none()
        if identifiers is None:
            return None
        job_id, document_id = identifiers
        document = cast(
            DocumentTable | None,
            await session.scalar(
                select(DocumentTable).where(DocumentTable.id == document_id).with_for_update()
            ),
        )
        job = cast(
            JobTable | None,
            await session.scalar(select(JobTable).where(JobTable.id == job_id).with_for_update()),
        )
        task = cast(
            TaskTable | None,
            await session.scalar(
                select(TaskTable).where(TaskTable.id == task_id).with_for_update()
            ),
        )
        if document is None or job is None or task is None:
            return None
        return _LockedTaskAggregate(document=document, job=job, task=task)

    async def _lock_event_aggregate(
        self,
        session: AsyncSession,
        event_id: str,
    ) -> _LockedEventAggregate | None:
        task_id = await session.scalar(
            select(OutboxEventTable.task_id)
            .join(TaskTable, TaskTable.id == OutboxEventTable.task_id)
            .join(JobTable, JobTable.id == TaskTable.job_id)
            .join(DocumentTable, DocumentTable.id == JobTable.document_id)
            .join(DatasetTable, DatasetTable.id == DocumentTable.dataset_id)
            .where(
                OutboxEventTable.id == event_id,
                DatasetTable.tenant_id == self._default_tenant_id,
            )
        )
        if task_id is None:
            return None
        task_aggregate = await self._lock_task_aggregate(session, task_id)
        if task_aggregate is None:
            return None
        event = cast(
            OutboxEventTable | None,
            await session.scalar(
                select(OutboxEventTable).where(OutboxEventTable.id == event_id).with_for_update()
            ),
        )
        if event is None or event.task_id != task_aggregate.task.id:
            return None
        return _LockedEventAggregate(
            document=task_aggregate.document,
            job=task_aggregate.job,
            task=task_aggregate.task,
            event=event,
        )
