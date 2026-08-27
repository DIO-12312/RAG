from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rag_mvp.adapters.chunkers.recursive import RecursiveChunker
from rag_mvp.adapters.parsers.text import TextParser
from rag_mvp.application.document_service import DocumentService
from rag_mvp.application.dto import CreateDatasetCommand, SubmitDocumentCommand
from rag_mvp.application.ingestion_service import IngestionService
from rag_mvp.domain.enums import JobStatus, OutboxStatus, TaskStatus
from rag_mvp.domain.errors import DomainError
from rag_mvp.ingestion.checkpoints import Checkpoint
from rag_mvp.ingestion.pipeline import IngestionPipeline
from rag_mvp.ingestion.worker import worker_once
from rag_mvp.outbox.finalizer import finalize_once
from rag_mvp.outbox.relay import relay_once
from rag_mvp.ports.metadata import CancelJobRequest, DeleteDatasetRequest
from tests.fakes.metadata import FakeMetadataRepository
from tests.fakes.model import FakeModelGateway
from tests.fakes.search_engine import FakeSearchEngine
from tests.fakes.storage import FakeObjectStorage
from tests.fakes.task_queue import FakeTaskQueue


async def _pending() -> tuple[
    datetime,
    FakeMetadataRepository,
    FakeObjectStorage,
    FakeTaskQueue,
    str,
]:
    now = datetime.now(UTC)
    repository = FakeMetadataRepository()
    storage = FakeObjectStorage()
    queue = FakeTaskQueue()
    documents = DocumentService(repository, storage, max_upload_bytes=1024)
    await documents.create_dataset(
        CreateDatasetCommand("request", "create", "Docs", "fake", 8, now, "dataset-1")
    )
    submitted = await documents.submit_document(
        SubmitDocumentCommand(
            "request",
            "submit",
            "dataset-1",
            "guide.txt",
            b"cancel source",
            None,
            None,
            "text-v1",
            800,
            120,
            "fake",
            now,
        )
    )
    return now, repository, storage, queue, submitted.job_id


@pytest.mark.asyncio
@pytest.mark.resilience
async def test_pending_cancel_cancels_task_and_unpublished_outbox() -> None:
    now, repository, _, _, job_id = await _pending()

    result = await repository.cancel_job(CancelJobRequest("cancel-key", job_id, now))
    repeated = await repository.cancel_job(CancelJobRequest("cancel-key", job_id, now))

    job = await repository.get_job(job_id)
    task = await repository.get_task_for_job(job_id)
    assert result.job_id == job_id and repeated.reused is True
    assert job is not None and job.status is JobStatus.CANCELLED
    assert task is not None and task.status is TaskStatus.CANCELLED
    assert next(iter(repository.outbox.values())).status is OutboxStatus.CANCELLED
    with pytest.raises(DomainError) as error:
        await repository.cancel_job(CancelJobRequest("another-key", job_id, now))
    assert error.value.failure.code == "JOB_ALREADY_TERMINAL"


@pytest.mark.asyncio
@pytest.mark.resilience
async def test_cancel_after_publish_before_claim_makes_worker_only_ack() -> None:
    now, repository, storage, queue, job_id = await _pending()
    await finalize_once(repository, storage, now, limit=10)
    await relay_once(repository, queue, now, limit=10)
    await repository.cancel_job(CancelJobRequest("cancel-key", job_id, now))
    model = FakeModelGateway(8)
    ingestion = IngestionService(
        repository,
        IngestionPipeline(
            storage, TextParser(), RecursiveChunker(800, 120), model, FakeSearchEngine()
        ),
    )

    assert await worker_once(queue, repository, ingestion, "worker", now)
    assert model.embed_calls == 0
    assert len(queue.acked_task_ids) == 1


@pytest.mark.asyncio
@pytest.mark.resilience
async def test_running_cancel_after_index_write_never_activates_version() -> None:
    now, repository, storage, queue, job_id = await _pending()
    await finalize_once(repository, storage, now, limit=10)
    await relay_once(repository, queue, now, limit=10)
    search = FakeSearchEngine()

    async def cancel_after_index(checkpoint: Checkpoint) -> None:
        if checkpoint is Checkpoint.AFTER_INDEX_WRITE:
            await repository.cancel_job(CancelJobRequest("cancel-key", job_id, now))

    ingestion = IngestionService(
        repository,
        IngestionPipeline(
            storage,
            TextParser(),
            RecursiveChunker(800, 120),
            FakeModelGateway(8),
            search,
            failpoint=cancel_after_index,
        ),
    )

    assert await worker_once(queue, repository, ingestion, "worker", now)
    job = await repository.get_job(job_id)
    document = next(iter(repository.documents.values()))
    assert job is not None and job.status is JobStatus.CANCELLED
    assert document.active_version is None
    assert await repository.visible_document_versions([document.id]) == {}
    assert search.record_count == 1


@pytest.mark.asyncio
@pytest.mark.resilience
async def test_dataset_delete_after_publish_before_claim_makes_worker_ack_only() -> None:
    now, repository, storage, queue, _job_id = await _pending()
    await finalize_once(repository, storage, now, limit=10)
    await relay_once(repository, queue, now, limit=10)
    await repository.delete_dataset(DeleteDatasetRequest("delete-dataset", "dataset-1", now))
    model = FakeModelGateway(8)
    ingestion = IngestionService(
        repository,
        IngestionPipeline(
            storage, TextParser(), RecursiveChunker(800, 120), model, FakeSearchEngine()
        ),
    )

    assert await worker_once(queue, repository, ingestion, "worker", now)
    assert model.embed_calls == 0
    assert len(queue.acked_task_ids) == 1
