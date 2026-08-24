"""MySQL implementation of authoritative metadata transactions."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from typing import cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rag_mvp.adapters.metadata.mappers import (
    dataset_from_table,
    document_from_table,
    job_from_table,
    task_from_table,
)
from rag_mvp.adapters.metadata.tables import (
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
from rag_mvp.domain.models import Dataset, Document, Job, Task
from rag_mvp.ports.metadata import SubmitIngestion, SubmitResult

SUBMIT_OPERATION = "SUBMIT_INGESTION"
MYSQL_DUPLICATE_KEY = 1062


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
