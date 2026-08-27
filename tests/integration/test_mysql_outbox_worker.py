"""Real MySQL tests for Finalizer, Relay, and Worker state transitions."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from rag_mvp.adapters.metadata.mysql import MySQLMetadataRepository
from rag_mvp.domain.enums import DocumentStatus, JobStatus, OutboxStatus, TaskStatus
from rag_mvp.domain.errors import DomainFailure
from rag_mvp.domain.models import Chunk, Dataset, Locator
from rag_mvp.ports.metadata import DeleteDatasetRequest, SubmitIngestion, SubmitResult


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
            idempotency_key="request-a",
            dataset_id="dataset-1",
            source_name="guide.txt",
            staging_key="staging/a",
            file_sha256="a" * 64,
            config_digest="b" * 64,
            now=now,
        )
    )


def _chunk(document_id: str) -> Chunk:
    return Chunk(
        id="c" * 16,
        document_id=document_id,
        index_version=1,
        ordinal=0,
        content_with_weight="traceable evidence",
        content_sha256="d" * 64,
        source_name="guide.txt",
        locator=Locator(start_line=1, end_line=1, metadata={"section": "intro"}),
        metadata={"kind": "text"},
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_outbox_transitions_delivery_dedup_and_atomic_completion(
    mysql_repository: tuple[MySQLMetadataRepository, AsyncEngine],
) -> None:
    repository, engine = mysql_repository
    now = datetime.now(UTC)
    submitted = await _submitted(repository, now)
    waiting = await repository.list_waiting_outbox(limit=10)

    assert len(waiting) == 1
    assert waiting[0].status is OutboxStatus.WAITING_OBJECT
    assert await repository.waiting_staging_keys() == ("staging/a",)
    assert await repository.mark_object_ready(waiting[0].id, "objects/document/source", now)
    assert not await repository.mark_object_ready(waiting[0].id, "objects/document/source", now)

    ready = await repository.list_ready_outbox(limit=10)
    assert [event.id for event in ready] == [waiting[0].id]
    assert await repository.mark_outbox_published(ready[0].id, now)
    assert not await repository.mark_outbox_published(ready[0].id, now)

    first_claim = await repository.claim_task(submitted.task_id, delivery_sequence=10, now=now)
    duplicate_claim = await repository.claim_task(submitted.task_id, delivery_sequence=10, now=now)
    redelivery_claim = await repository.claim_task(submitted.task_id, delivery_sequence=11, now=now)

    assert first_claim is not None
    assert duplicate_claim is None
    assert redelivery_claim is not None
    assert redelivery_claim.task.attempt == 2
    assert redelivery_claim.task.last_delivery_sequence == 11
    assert await repository.complete_ingestion(
        submitted.task_id,
        [_chunk(submitted.document_id)],
        now,
    )
    assert not await repository.complete_ingestion(
        submitted.task_id,
        [_chunk(submitted.document_id)],
        now,
    )
    assert await repository.visible_document_versions([submitted.document_id, "missing"]) == {
        submitted.document_id: 1
    }

    task = await repository.get_task(submitted.task_id)
    job = await repository.get_job(submitted.job_id)
    document = await repository.get_document(submitted.document_id)
    assert task is not None and task.status is TaskStatus.SUCCEEDED
    assert job is not None and job.status is JobStatus.SUCCEEDED
    assert document is not None and document.status is DocumentStatus.READY
    assert document.active_version == 1
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT COUNT(*) FROM chunk_manifests")) == 1
        assert await connection.scalar(text("SELECT status FROM index_builds LIMIT 1")) == "ACTIVE"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_dataset_cleanup_outbox_is_publishable_without_document(
    mysql_repository: tuple[MySQLMetadataRepository, AsyncEngine],
) -> None:
    """Dataset-scoped cleanup has no document row but must still reach Relay."""

    repository, _engine = mysql_repository
    now = datetime.now(UTC)
    await _submitted(repository, now)
    deleted = await repository.delete_dataset(
        DeleteDatasetRequest("delete-dataset-key", "dataset-1", now)
    )

    ready = await repository.list_ready_outbox(limit=10)

    assert [event.task_id for event in ready] == [deleted.task_id]
    assert await repository.mark_outbox_published(ready[0].id, now)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_finalizer_exhaustion_atomically_fails_and_releases_fingerprint(
    mysql_repository: tuple[MySQLMetadataRepository, AsyncEngine],
) -> None:
    repository, engine = mysql_repository
    now = datetime.now(UTC)
    submitted = await _submitted(repository, now)
    event = (await repository.list_waiting_outbox(limit=1))[0]

    assert not await repository.record_finalization_failure(event.id, 2, now)
    assert await repository.record_finalization_failure(event.id, 2, now)
    assert not await repository.record_finalization_failure(event.id, 2, now)

    task = await repository.get_task(submitted.task_id)
    job = await repository.get_job(submitted.job_id)
    document = await repository.get_document(submitted.document_id)
    assert task is not None and task.status is TaskStatus.FAILED
    assert task.error is not None and task.error.code == "OBJECT_FINALIZATION_FAILED"
    assert job is not None and job.status is JobStatus.FAILED
    assert job.retryable is False
    assert document is not None and document.status is DocumentStatus.FAILED
    async with engine.connect() as connection:
        assert (
            await connection.scalar(text("SELECT status FROM outbox_events LIMIT 1")) == "CANCELLED"
        )
        assert (
            await connection.scalar(text("SELECT state FROM ingestion_fingerprints LIMIT 1"))
            == "RELEASED"
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_deleted_generation_fence_prevents_object_ready_and_task_claim(
    mysql_repository: tuple[MySQLMetadataRepository, AsyncEngine],
) -> None:
    repository, engine = mysql_repository
    now = datetime.now(UTC)
    submitted = await _submitted(repository, now)
    event = (await repository.list_waiting_outbox(limit=1))[0]

    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE documents SET status = 'DELETED', "
                "lifecycle_generation = lifecycle_generation + 1"
            )
        )

    assert not await repository.mark_object_ready(event.id, "objects/document/source", now)
    assert await repository.claim_task(submitted.task_id, delivery_sequence=1, now=now) is None
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT object_key FROM documents LIMIT 1")) is None
        assert (
            await connection.scalar(text("SELECT status FROM outbox_events LIMIT 1"))
            == "WAITING_OBJECT"
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fail_task_persists_retryability_and_terminal_state_once(
    mysql_repository: tuple[MySQLMetadataRepository, AsyncEngine],
) -> None:
    repository, engine = mysql_repository
    now = datetime.now(UTC)
    submitted = await _submitted(repository, now)
    event = (await repository.list_waiting_outbox(limit=1))[0]
    assert await repository.mark_object_ready(event.id, "objects/document/source", now)
    assert await repository.claim_task(submitted.task_id, delivery_sequence=1, now=now)
    failure = DomainFailure("MODEL_UNAVAILABLE", "temporary provider failure", retryable=True)

    assert await repository.fail_task(submitted.task_id, failure, now)
    assert not await repository.fail_task(submitted.task_id, failure, now)

    task = await repository.get_task(submitted.task_id)
    job = await repository.get_job(submitted.job_id)
    assert task is not None and task.status is TaskStatus.FAILED
    assert job is not None and job.status is JobStatus.FAILED and job.retryable is True
    async with engine.connect() as connection:
        assert (
            await connection.scalar(text("SELECT state FROM ingestion_fingerprints LIMIT 1"))
            == "FAILED_RETRYABLE"
        )
