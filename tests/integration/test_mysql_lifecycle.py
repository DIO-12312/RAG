"""Real MySQL tests for cancel, delete, and cleanup lifecycle semantics."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from rag_mvp.adapters.metadata.mysql import MySQLMetadataRepository
from rag_mvp.domain.enums import (
    DatasetStatus,
    DocumentStatus,
    FingerprintState,
    JobStatus,
    JobType,
    OutboxStatus,
    TaskStatus,
    TaskType,
)
from rag_mvp.domain.errors import DomainError
from rag_mvp.domain.models import Chunk, Dataset, Locator
from rag_mvp.ports.metadata import (
    CancelJobRequest,
    DeleteDatasetRequest,
    DeleteDocumentRequest,
    SubmitIngestion,
    SubmitResult,
)


async def _submitted(
    repository: MySQLMetadataRepository,
    now: datetime,
) -> SubmitResult:
    await repository.create_dataset(
        Dataset(
            id="dataset-1",
            tenant_id="default_tenant",
            name="Docs",
            embedding_model="fake-embedding",
            embedding_dimension=8,
            created_at=now,
        )
    )
    return await repository.submit_ingestion(
        SubmitIngestion(
            idempotency_key="submit",
            dataset_id="dataset-1",
            source_name="guide.txt",
            staging_key="staging/submit",
            file_sha256="a" * 64,
            config_digest="b" * 64,
            now=now,
        )
    )


def _chunk(document_id: str, index_version: int = 1) -> Chunk:
    return Chunk(
        id="c" * 16,
        document_id=document_id,
        index_version=index_version,
        ordinal=0,
        content_with_weight="lifecycle evidence",
        content_sha256="d" * 64,
        source_name="guide.txt",
        locator=Locator(start_line=1, end_line=1),
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pending_cancel_is_immediate_idempotent_and_withdraws_outbox(
    mysql_repository: tuple[MySQLMetadataRepository, AsyncEngine],
) -> None:
    repository, engine = mysql_repository
    now = datetime.now(UTC)
    submitted = await _submitted(repository, now)
    request = CancelJobRequest("cancel-key", submitted.job_id, now)

    first = await repository.cancel_job(request)
    repeated = await repository.cancel_job(request)

    job = await repository.get_job(submitted.job_id)
    task = await repository.get_task(submitted.task_id)
    assert first.job_id == submitted.job_id and first.reused is False
    assert repeated.job_id == first.job_id and repeated.reused is True
    assert job is not None and job.status is JobStatus.CANCELLED
    assert job.cancel_requested_at is not None
    assert task is not None and task.status is TaskStatus.CANCELLED
    assert await repository.claim_task(submitted.task_id, 1, now) is None
    async with engine.connect() as connection:
        assert (
            await connection.scalar(text("SELECT status FROM outbox_events LIMIT 1")) == "CANCELLED"
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_running_cancel_converges_at_completion_without_activating_version(
    mysql_repository: tuple[MySQLMetadataRepository, AsyncEngine],
) -> None:
    repository, engine = mysql_repository
    now = datetime.now(UTC)
    submitted = await _submitted(repository, now)
    event = (await repository.list_waiting_outbox(1))[0]
    assert await repository.mark_object_ready(event.id, "objects/document/source", now)
    assert await repository.claim_task(submitted.task_id, 1, now)

    cancelled = await repository.cancel_job(CancelJobRequest("cancel-key", submitted.job_id, now))
    running_job = await repository.get_job(submitted.job_id)
    assert cancelled.reused is False
    assert running_job is not None and running_job.status is JobStatus.RUNNING
    assert running_job.cancel_requested_at is not None
    assert not await repository.complete_ingestion(
        submitted.task_id,
        [_chunk(submitted.document_id)],
        now,
    )

    job = await repository.get_job(submitted.job_id)
    task = await repository.get_task(submitted.task_id)
    document = await repository.get_document(submitted.document_id)
    assert job is not None and job.status is JobStatus.CANCELLED
    assert task is not None and task.status is TaskStatus.CANCELLED
    assert document is not None and document.active_version is None
    assert await repository.visible_document_versions([submitted.document_id]) == {}
    async with engine.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT COUNT(*) FROM jobs WHERE type = 'CLEANUP_INDEX_VERSION'")
            )
            == 1
        )
        assert (
            await connection.scalar(text("SELECT status FROM index_builds LIMIT 1")) == "ABANDONED"
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_delete_hides_immediately_cancels_ingest_and_cleanup_honors_generation(
    mysql_repository: tuple[MySQLMetadataRepository, AsyncEngine],
) -> None:
    repository, engine = mysql_repository
    now = datetime.now(UTC)
    submitted = await _submitted(repository, now)
    event = (await repository.list_waiting_outbox(1))[0]
    assert await repository.mark_object_ready(event.id, "objects/document/source", now)
    assert await repository.claim_task(submitted.task_id, 1, now)

    request = DeleteDocumentRequest("delete-key", submitted.document_id, now)
    deleted = await repository.delete_document(request)
    repeated = await repository.delete_document(request)

    assert repeated.job_id == deleted.job_id and repeated.reused is True
    assert await repository.visible_document_versions([submitted.document_id]) == {}
    assert not await repository.complete_ingestion(
        submitted.task_id,
        [_chunk(submitted.document_id)],
        now,
    )
    assert await repository.claim_task(submitted.task_id, 2, now) is None

    document = await repository.get_document(submitted.document_id)
    ingest_job = await repository.get_job(submitted.job_id)
    ingest_task = await repository.get_task(submitted.task_id)
    cleanup_job = await repository.get_job(deleted.job_id)
    cleanup_task = await repository.get_task(deleted.task_id)
    assert document is not None and document.status is DocumentStatus.DELETED
    assert document.lifecycle_generation == 1
    assert ingest_job is not None and ingest_job.status is JobStatus.CANCELLED
    assert ingest_task is not None and ingest_task.status is TaskStatus.CANCELLED
    assert cleanup_job is not None and cleanup_job.status is JobStatus.PENDING
    assert cleanup_task is not None and cleanup_task.status is TaskStatus.PENDING

    assert await repository.claim_task(deleted.task_id, 3, now)
    assert await repository.complete_cleanup(deleted.task_id, now)
    assert not await repository.complete_cleanup(deleted.task_id, now)
    cleanup_job = await repository.get_job(deleted.job_id)
    cleanup_task = await repository.get_task(deleted.task_id)
    assert cleanup_job is not None and cleanup_job.status is JobStatus.SUCCEEDED
    assert cleanup_task is not None and cleanup_task.status is TaskStatus.SUCCEEDED
    async with engine.connect() as connection:
        assert (
            await connection.scalar(text("SELECT state FROM ingestion_fingerprints LIMIT 1"))
            == "RELEASED"
        )
        assert await connection.scalar(text("SELECT COUNT(*) FROM index_builds")) == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_new_delete_key_for_deleted_document_is_rejected(
    mysql_repository: tuple[MySQLMetadataRepository, AsyncEngine],
) -> None:
    repository, _engine = mysql_repository
    now = datetime.now(UTC)
    submitted = await _submitted(repository, now)
    await repository.delete_document(DeleteDocumentRequest("delete-1", submitted.document_id, now))

    with pytest.raises(DomainError) as error:
        await repository.delete_document(
            DeleteDocumentRequest("delete-2", submitted.document_id, now)
        )

    assert error.value.failure.code == "DOCUMENT_ALREADY_DELETED"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_delete_dataset_atomically_fences_children_and_enqueues_cleanup(
    mysql_repository: tuple[MySQLMetadataRepository, AsyncEngine],
) -> None:
    repository, engine = mysql_repository
    now = datetime.now(UTC)
    submitted = await _submitted(repository, now)

    deleted = await repository.delete_dataset(
        DeleteDatasetRequest("delete-dataset-key", "dataset-1", now)
    )
    repeated = await repository.delete_dataset(
        DeleteDatasetRequest("delete-dataset-key", "dataset-1", now)
    )

    dataset = await repository.get_dataset("dataset-1")
    document = await repository.get_document(submitted.document_id)
    ingest_job = await repository.get_job(submitted.job_id)
    ingest_task = await repository.get_task(submitted.task_id)
    cleanup_job = await repository.get_job(deleted.job_id)
    cleanup_task = await repository.get_task(deleted.task_id)
    assert repeated == type(deleted)(
        deleted.dataset_id,
        deleted.job_id,
        deleted.task_id,
        True,
    )
    assert dataset is not None and dataset.status is DatasetStatus.DELETING
    assert dataset.lifecycle_generation == 1
    assert document is not None and document.status is DocumentStatus.DELETED
    assert document.lifecycle_generation == 1
    assert ingest_job is not None and ingest_job.status is JobStatus.CANCELLED
    assert ingest_task is not None and ingest_task.status is TaskStatus.CANCELLED
    assert cleanup_job is not None and cleanup_job.type is JobType.DELETE_DATASET
    assert cleanup_job.document_id is None and cleanup_job.dataset_id == "dataset-1"
    assert cleanup_task is not None and cleanup_task.type is TaskType.CLEANUP_DATASET
    assert await repository.visible_document_versions([submitted.document_id]) == {}

    async with engine.connect() as connection:
        assert await connection.scalar(
            text("SELECT state FROM ingestion_fingerprints LIMIT 1")
        ) == FingerprintState.RELEASED
        assert await connection.scalar(
            text("SELECT status FROM outbox_events WHERE task_id = :task_id"),
            {"task_id": submitted.task_id},
        ) == OutboxStatus.CANCELLED
        assert await connection.scalar(
            text("SELECT status FROM outbox_events WHERE task_id = :task_id"),
            {"task_id": deleted.task_id},
        ) == OutboxStatus.READY_TO_PUBLISH


@pytest.mark.integration
@pytest.mark.asyncio
async def test_delete_dataset_rejects_new_key_and_new_ingestion(
    mysql_repository: tuple[MySQLMetadataRepository, AsyncEngine],
) -> None:
    repository, _engine = mysql_repository
    now = datetime.now(UTC)
    await _submitted(repository, now)
    await repository.delete_dataset(DeleteDatasetRequest("delete-1", "dataset-1", now))

    with pytest.raises(DomainError) as deleting:
        await repository.delete_dataset(DeleteDatasetRequest("delete-2", "dataset-1", now))
    assert deleting.value.failure.code == "DATASET_DELETION_IN_PROGRESS"

    with pytest.raises(DomainError) as submitted:
        await repository.submit_ingestion(
            SubmitIngestion(
                idempotency_key="submit-after-delete",
                dataset_id="dataset-1",
                source_name="late.txt",
                staging_key="staging/late",
                file_sha256="c" * 64,
                config_digest="d" * 64,
                now=now,
            )
        )
    assert submitted.value.failure.code == "DATASET_DELETING"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_dataset_cleanup_snapshot_and_final_purge_remove_complete_aggregate(
    mysql_repository: tuple[MySQLMetadataRepository, AsyncEngine],
) -> None:
    repository, engine = mysql_repository
    now = datetime.now(UTC)
    submitted = await _submitted(repository, now)
    waiting = (await repository.list_waiting_outbox(1))[0]
    assert await repository.mark_object_ready(waiting.id, "objects/dataset-1/source", now)
    deleted = await repository.delete_dataset(
        DeleteDatasetRequest("delete-dataset", "dataset-1", now)
    )

    assert await repository.claim_task(deleted.task_id, 1, now)
    assert await repository.dataset_cleanup_object_keys(deleted.task_id) == (
        "objects/dataset-1/source",
        "staging/submit",
    )
    assert await repository.finalize_dataset_cleanup(deleted.task_id, now)
    assert not await repository.finalize_dataset_cleanup(deleted.task_id, now)
    assert await repository.get_dataset("dataset-1") is None
    assert await repository.get_document(submitted.document_id) is None
    assert await repository.get_job(deleted.job_id) is None
    assert await repository.get_task(deleted.task_id) is None

    async with engine.connect() as connection:
        for table_name in (
            "datasets",
            "documents",
            "ingestion_fingerprints",
            "jobs",
            "tasks",
            "outbox_events",
            "index_builds",
            "chunk_manifests",
            "idempotency_records",
        ):
            assert await connection.scalar(text(f"SELECT COUNT(*) FROM {table_name}")) == 0
