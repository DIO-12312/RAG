from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rag_mvp.domain.enums import JobStatus, OutboxStatus, TaskStatus
from rag_mvp.domain.errors import DomainFailure
from rag_mvp.domain.models import Chunk, Dataset, Locator
from rag_mvp.ports.metadata import SubmitIngestion
from tests.fakes.metadata import FakeMetadataRepository, InjectedRepositoryFailure


def _dataset(now: datetime) -> Dataset:
    return Dataset(
        id="dataset-1",
        name="Docs",
        embedding_model="fake-embedding",
        embedding_dimension=8,
        created_at=now,
    )


def _submission(*, idempotency_key: str, staging_key: str, now: datetime) -> SubmitIngestion:
    return SubmitIngestion(
        idempotency_key=idempotency_key,
        dataset_id="dataset-1",
        source_name="guide.txt",
        staging_key=staging_key,
        file_sha256="a" * 64,
        config_digest="b" * 64,
        now=now,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("second_idempotency_key", ("request-a", "request-b"))
async def test_submit_atomically_creates_task_and_waiting_outbox_and_deduplicates(
    second_idempotency_key: str,
) -> None:
    now = datetime.now(UTC)
    repository = FakeMetadataRepository()
    await repository.create_dataset(_dataset(now))

    first = await repository.submit_ingestion(
        _submission(idempotency_key="request-a", staging_key="staging/a", now=now)
    )
    duplicate = await repository.submit_ingestion(
        _submission(idempotency_key=second_idempotency_key, staging_key="staging/b", now=now)
    )

    assert first.reused is False
    assert first.staging_referenced is True
    assert duplicate.reused is True
    assert duplicate.staging_referenced is False
    assert duplicate.document_id == first.document_id
    assert duplicate.job_id == first.job_id
    assert repository.counts() == {
        "documents": 1,
        "fingerprints": 1,
        "jobs": 1,
        "tasks": 1,
        "outbox": 1,
        "index_builds": 1,
    }
    waiting = await repository.list_waiting_outbox(limit=10)
    assert [event.status for event in waiting] == [OutboxStatus.WAITING_OBJECT]
    assert waiting[0].task_id == first.task_id


@pytest.mark.asyncio
async def test_submit_failure_does_not_leave_partial_metadata() -> None:
    now = datetime.now(UTC)
    repository = FakeMetadataRepository()
    await repository.create_dataset(_dataset(now))
    repository.fail_next_submit = True

    with pytest.raises(InjectedRepositoryFailure):
        await repository.submit_ingestion(
            _submission(idempotency_key="request-a", staging_key="staging/a", now=now)
        )

    assert repository.counts() == {
        "documents": 0,
        "fingerprints": 0,
        "jobs": 0,
        "tasks": 0,
        "outbox": 0,
        "index_builds": 0,
    }


@pytest.mark.asyncio
async def test_finalizer_transition_and_task_claim_are_conditional() -> None:
    now = datetime.now(UTC)
    repository = FakeMetadataRepository()
    await repository.create_dataset(_dataset(now))
    submitted = await repository.submit_ingestion(
        _submission(idempotency_key="request-a", staging_key="staging/a", now=now)
    )
    event = (await repository.list_waiting_outbox(limit=1))[0]

    assert await repository.mark_object_ready(event.id, "objects/doc", now) is True
    assert await repository.mark_object_ready(event.id, "objects/doc", now) is False
    ready = await repository.list_ready_outbox(limit=1)
    assert ready[0].status is OutboxStatus.READY_TO_PUBLISH
    assert await repository.mark_outbox_published(ready[0].id, now) is True
    assert await repository.mark_outbox_published(ready[0].id, now) is False

    claim = await repository.claim_task(submitted.task_id, delivery_sequence=1, now=now)
    duplicate_claim = await repository.claim_task(submitted.task_id, delivery_sequence=1, now=now)
    redelivery_claim = await repository.claim_task(submitted.task_id, delivery_sequence=2, now=now)
    assert claim is not None
    assert claim.task.status is TaskStatus.RUNNING
    assert duplicate_claim is None
    assert redelivery_claim is not None
    assert redelivery_claim.task.attempt == 2
    assert redelivery_claim.task.last_delivery_sequence == 2
    assert await repository.get_dataset("dataset-1") is not None
    assert await repository.get_document(submitted.document_id) is not None
    assert await repository.get_job(submitted.job_id) is not None
    assert await repository.get_task(submitted.task_id) is not None
    task_for_job = await repository.get_task_for_job(submitted.job_id)
    assert task_for_job is not None and task_for_job.id == submitted.task_id
    assert await repository.get_task_for_job("missing") is None


@pytest.mark.asyncio
async def test_complete_and_fail_are_conditional_and_visibility_uses_active_version() -> None:
    now = datetime.now(UTC)
    repository = FakeMetadataRepository()
    await repository.create_dataset(_dataset(now))
    succeeded = await repository.submit_ingestion(
        _submission(idempotency_key="request-a", staging_key="staging/a", now=now)
    )
    await repository.claim_task(succeeded.task_id, delivery_sequence=1, now=now)
    chunk = Chunk(
        id="chunk-1",
        document_id=succeeded.document_id,
        index_version=1,
        ordinal=0,
        content_with_weight="hello",
        content_sha256="c" * 64,
        source_name="guide.txt",
        locator=Locator(start_line=1, end_line=1),
    )

    assert await repository.complete_ingestion(succeeded.task_id, [chunk], now) is True
    assert await repository.complete_ingestion(succeeded.task_id, [chunk], now) is False
    assert await repository.visible_document_versions([succeeded.document_id, "missing"]) == {
        succeeded.document_id: 1
    }
    assert (await repository.get_job(succeeded.job_id)).status is JobStatus.SUCCEEDED  # type: ignore[union-attr]

    failed = await repository.submit_ingestion(
        SubmitIngestion(
            idempotency_key="request-b",
            dataset_id="dataset-1",
            source_name="other.txt",
            staging_key="staging/b",
            file_sha256="d" * 64,
            config_digest="b" * 64,
            now=now,
        )
    )
    await repository.claim_task(failed.task_id, delivery_sequence=2, now=now)
    failure = DomainFailure("MODEL_UNAVAILABLE", "temporary", retryable=True)

    assert await repository.fail_task(failed.task_id, failure, now) is True
    assert await repository.fail_task(failed.task_id, failure, now) is False
    failed_job = await repository.get_job(failed.job_id)
    assert failed_job is not None
    assert failed_job.status is JobStatus.FAILED
    assert failed_job.retryable is True
