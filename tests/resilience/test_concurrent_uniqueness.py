from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from rag_mvp.application.document_service import DocumentService
from rag_mvp.application.dto import CreateDatasetCommand, SubmitDocumentCommand
from rag_mvp.domain.errors import DomainFailure
from rag_mvp.outbox.finalizer import finalize_once
from rag_mvp.ports.metadata import RetryJobRequest
from tests.fakes.metadata import FakeMetadataRepository
from tests.fakes.storage import FakeObjectStorage


def _submit(
    key: str, now: datetime, *, target_document_id: str | None = None, chunk_size: int = 800
) -> SubmitDocumentCommand:
    return SubmitDocumentCommand(
        "request",
        key,
        "dataset-1",
        "guide.txt",
        b"concurrent source",
        None,
        target_document_id,
        "text-v1",
        chunk_size,
        120,
        "fake",
        now,
    )


async def _service() -> tuple[DocumentService, FakeMetadataRepository, FakeObjectStorage, datetime]:
    now = datetime.now(UTC)
    repository = FakeMetadataRepository()
    storage = FakeObjectStorage()
    service = DocumentService(repository, storage, max_upload_bytes=1024)
    await service.create_dataset(
        CreateDatasetCommand("request", "create", "Docs", "fake", 8, now, "dataset-1")
    )
    return service, repository, storage, now


@pytest.mark.asyncio
@pytest.mark.resilience
async def test_concurrent_same_file_upload_has_one_canonical_job_and_no_loser_staging() -> None:
    service, repository, storage, now = await _service()

    first, second = await asyncio.gather(
        service.submit_document(_submit("upload-a", now)),
        service.submit_document(_submit("upload-b", now)),
    )

    assert first.document_id == second.document_id
    assert first.job_id == second.job_id
    assert repository.counts()["documents"] == 1
    assert repository.counts()["jobs"] == 1
    assert sum(key.startswith("staging/") for key in storage.objects) == 1


@pytest.mark.asyncio
@pytest.mark.resilience
async def test_concurrent_retry_calls_create_one_active_child() -> None:
    service, repository, storage, now = await _service()
    submitted = await service.submit_document(_submit("upload", now))
    await finalize_once(repository, storage, now, limit=10)
    task = await repository.get_task_for_job(submitted.job_id)
    assert task is not None
    await repository.claim_task(task.id, 1, now)
    await repository.fail_task(
        task.id, DomainFailure("MODEL_UNAVAILABLE", "temporary", retryable=True), now
    )

    results = await asyncio.gather(
        *(
            repository.retry_job(RetryJobRequest(f"retry-{index}", submitted.job_id, now, 3))
            for index in range(10)
        )
    )

    assert len({result.job_id for result in results}) == 1
    original = await repository.get_job(submitted.job_id)
    assert original is not None and original.retry_count == 1
    assert len([job for job in repository.jobs.values() if job.retry_of_job_id]) == 1


@pytest.mark.asyncio
@pytest.mark.resilience
async def test_concurrent_rebuilds_allocate_distinct_index_versions() -> None:
    service, repository, _, now = await _service()
    submitted = await service.submit_document(_submit("upload", now))
    document = repository.documents[submitted.document_id]
    repository.documents[document.id] = document.__class__(
        id=document.id,
        dataset_id=document.dataset_id,
        source_name=document.source_name,
        file_sha256=document.file_sha256,
        status=document.status,
        active_version=1,
        next_index_version=2,
        lifecycle_generation=document.lifecycle_generation,
        created_at=document.created_at,
        object_key="objects/document/source",
    )

    first, second = await asyncio.gather(
        service.submit_document(
            _submit("rebuild-a", now, target_document_id=document.id, chunk_size=900)
        ),
        service.submit_document(
            _submit("rebuild-b", now, target_document_id=document.id, chunk_size=901)
        ),
    )

    versions = {
        repository.jobs[first.job_id].index_version,
        repository.jobs[second.job_id].index_version,
    }
    assert versions == {2, 3}
    assert repository.documents[document.id].next_index_version == 4
