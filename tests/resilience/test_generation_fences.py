from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rag_mvp.adapters.chunkers.recursive import RecursiveChunker
from rag_mvp.adapters.parsers.text import TextParser
from rag_mvp.application.cleanup_service import CleanupService
from rag_mvp.application.document_service import DocumentService
from rag_mvp.application.dto import CreateDatasetCommand, SubmitDocumentCommand
from rag_mvp.application.ingestion_service import IngestionService
from rag_mvp.domain.enums import DocumentStatus, JobStatus, OutboxStatus
from rag_mvp.ingestion.checkpoints import Checkpoint
from rag_mvp.ingestion.pipeline import IngestionPipeline
from rag_mvp.ingestion.worker import worker_once
from rag_mvp.outbox.finalizer import finalize_once
from rag_mvp.outbox.relay import relay_once
from rag_mvp.ports.metadata import CancelJobRequest, DeleteDocumentRequest
from tests.fakes.metadata import FakeMetadataRepository
from tests.fakes.model import FakeModelGateway
from tests.fakes.search_engine import FakeSearchEngine
from tests.fakes.storage import FakeObjectStorage
from tests.fakes.task_queue import FakeTaskQueue


async def _harness() -> tuple[
    datetime,
    FakeMetadataRepository,
    FakeObjectStorage,
    FakeTaskQueue,
    FakeSearchEngine,
    str,
    str,
]:
    now = datetime.now(UTC)
    repository = FakeMetadataRepository()
    storage = FakeObjectStorage()
    queue = FakeTaskQueue()
    search = FakeSearchEngine()
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
            b"generation fence",
            None,
            None,
            "text-v1",
            800,
            120,
            "fake",
            now,
        )
    )
    return now, repository, storage, queue, search, submitted.job_id, submitted.document_id


@pytest.mark.asyncio
@pytest.mark.resilience
async def test_cancel_after_index_write_creates_version_cleanup_task() -> None:
    now, repository, storage, queue, search, job_id, document_id = await _harness()
    await finalize_once(repository, storage, now, limit=10)
    await relay_once(repository, queue, now, limit=10)

    async def cancel(checkpoint: Checkpoint) -> None:
        if checkpoint is Checkpoint.AFTER_INDEX_WRITE:
            await repository.cancel_job(CancelJobRequest("cancel", job_id, now))

    ingestion = IngestionService(
        repository,
        IngestionPipeline(
            storage,
            TextParser(),
            RecursiveChunker(800, 120),
            FakeModelGateway(8),
            search,
            failpoint=cancel,
        ),
    )
    cleanup = CleanupService(repository, search, storage)

    assert await worker_once(queue, repository, ingestion, "ingest", now, cleanup=cleanup)
    assert search.record_count == 1
    assert await relay_once(repository, queue, now, limit=10) == 1
    assert await worker_once(queue, repository, ingestion, "cleanup", now, cleanup=cleanup)

    system_jobs = [job for job in repository.jobs.values() if job.is_system]
    assert len(system_jobs) == 1 and system_jobs[0].status is JobStatus.SUCCEEDED
    assert search.record_count == 0
    assert repository.documents[document_id].active_version is None


@pytest.mark.asyncio
@pytest.mark.resilience
async def test_delete_after_index_write_never_reactivates_and_cleanup_removes_everything() -> None:
    now, repository, storage, queue, search, job_id, document_id = await _harness()
    await finalize_once(repository, storage, now, limit=10)
    await relay_once(repository, queue, now, limit=10)

    async def delete(checkpoint: Checkpoint) -> None:
        if checkpoint is Checkpoint.AFTER_INDEX_WRITE:
            await repository.delete_document(DeleteDocumentRequest("delete", document_id, now))

    ingestion = IngestionService(
        repository,
        IngestionPipeline(
            storage,
            TextParser(),
            RecursiveChunker(800, 120),
            FakeModelGateway(8),
            search,
            failpoint=delete,
        ),
    )
    cleanup = CleanupService(repository, search, storage)

    assert await worker_once(queue, repository, ingestion, "ingest", now, cleanup=cleanup)
    assert repository.documents[document_id].status is DocumentStatus.DELETED
    assert repository.documents[document_id].active_version is None
    assert search.record_count == 1
    assert await relay_once(repository, queue, now, limit=10) == 1
    assert await worker_once(queue, repository, ingestion, "cleanup", now, cleanup=cleanup)
    assert repository.documents[document_id].status is DocumentStatus.DELETED
    assert search.record_count == 0
    assert not await storage.exists(f"objects/{document_id}/source")
    assert repository.jobs[job_id].status is JobStatus.CANCELLED


@pytest.mark.asyncio
@pytest.mark.resilience
async def test_delete_between_promote_and_ready_compensates_final_object() -> None:
    now, repository, storage, _, _, _, document_id = await _harness()

    async def delete_after_promote() -> None:
        await repository.delete_document(DeleteDocumentRequest("delete", document_id, now))

    assert (
        await finalize_once(
            repository,
            storage,
            now,
            limit=10,
            after_promote=delete_after_promote,
        )
        == 0
    )

    assert repository.documents[document_id].status is DocumentStatus.DELETED
    assert not await storage.exists(f"objects/{document_id}/source")
    ingest_events = [
        event
        for event in repository.outbox.values()
        if repository.tasks[event.task_id].job_id
        != next(job.id for job in repository.jobs.values() if job.type.value == "DELETE_DOCUMENT")
    ]
    assert all(event.status is OutboxStatus.CANCELLED for event in ingest_events)
