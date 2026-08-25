from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rag_mvp.adapters.chunkers.recursive import RecursiveChunker
from rag_mvp.adapters.parsers.text import TextParser
from rag_mvp.application.document_service import DocumentService
from rag_mvp.application.dto import CreateDatasetCommand, SubmitDocumentCommand
from rag_mvp.application.ingestion_service import IngestionService
from rag_mvp.domain.enums import JobStatus, TaskStatus
from rag_mvp.domain.errors import DomainFailure
from rag_mvp.ingestion.pipeline import IngestionPipeline
from rag_mvp.ingestion.worker import worker_once
from rag_mvp.outbox.finalizer import finalize_once
from rag_mvp.outbox.relay import relay_once
from tests.fakes.metadata import FakeMetadataRepository
from tests.fakes.model import FakeModelGateway
from tests.fakes.search_engine import FakeSearchEngine
from tests.fakes.storage import FakeObjectStorage
from tests.fakes.task_queue import FakeTaskQueue


class FailingModelGateway(FakeModelGateway):
    async def embed(self, texts: list[str]) -> list[tuple[float, ...]]:
        del texts
        raise ConnectionError("model unavailable")


@pytest.mark.asyncio
async def test_worker_claims_executes_completes_then_acks() -> None:
    now = datetime.now(UTC)
    repository = FakeMetadataRepository()
    storage = FakeObjectStorage()
    queue = FakeTaskQueue()
    model = FakeModelGateway(8)
    search = FakeSearchEngine()
    documents = DocumentService(repository, storage, max_upload_bytes=1024)
    await documents.create_dataset(
        CreateDatasetCommand("trace", "create", "Docs", "fake", 8, now, "dataset-1")
    )
    submitted = await documents.submit_document(
        SubmitDocumentCommand(
            "trace",
            "submit",
            "dataset-1",
            "guide.txt",
            b"hello retrieval",
            None,
            None,
            "text-v1",
            800,
            120,
            "fake",
            now,
        )
    )
    await finalize_once(repository, storage, now, limit=10)
    await relay_once(repository, queue, now, limit=10)
    ingestion = IngestionService(
        repository,
        IngestionPipeline(
            storage,
            TextParser(),
            RecursiveChunker(800, 120),
            model,
            search,
        ),
    )

    assert await worker_once(queue, repository, ingestion, "worker-1", now) is True

    job = await repository.get_job(submitted.job_id)
    task = next(iter(repository.tasks.values()))
    assert job is not None and job.status is JobStatus.SUCCEEDED
    assert task.status is TaskStatus.SUCCEEDED
    assert queue.acked_task_ids == [task.id]
    assert search.record_count == 1

    await queue.publish(task.id)
    assert await worker_once(queue, repository, ingestion, "worker-2", now) is True
    assert model.embed_calls == 1
    assert search.upsert_calls == 1
    assert queue.acked_task_ids == [task.id, task.id]


@pytest.mark.asyncio
async def test_worker_returns_false_when_queue_is_empty() -> None:
    now = datetime.now(UTC)
    repository = FakeMetadataRepository()
    storage = FakeObjectStorage()
    ingestion = IngestionService(
        repository,
        IngestionPipeline(
            storage,
            TextParser(),
            RecursiveChunker(800, 120),
            FakeModelGateway(8),
            FakeSearchEngine(),
        ),
    )

    assert await worker_once(FakeTaskQueue(), repository, ingestion, "worker-1", now) is False


@pytest.mark.asyncio
async def test_worker_naks_retryable_failure_then_fails_at_delivery_limit() -> None:
    now = datetime.now(UTC)
    repository = FakeMetadataRepository()
    storage = FakeObjectStorage()
    queue = FakeTaskQueue()
    documents = DocumentService(repository, storage, max_upload_bytes=1024)
    await documents.create_dataset(
        CreateDatasetCommand("trace", "create", "Docs", "fake", 8, now, "dataset-1")
    )
    submitted = await documents.submit_document(
        SubmitDocumentCommand(
            "trace",
            "submit",
            "dataset-1",
            "guide.txt",
            b"hello retrieval",
            None,
            None,
            "text-v1",
            800,
            120,
            "fake",
            now,
        )
    )
    await finalize_once(repository, storage, now, limit=10)
    await relay_once(repository, queue, now, limit=10)
    ingestion = IngestionService(
        repository,
        IngestionPipeline(
            storage,
            TextParser(),
            RecursiveChunker(800, 120),
            FailingModelGateway(8),
            FakeSearchEngine(),
        ),
    )

    assert await worker_once(queue, repository, ingestion, "worker-1", now, max_deliveries=2)
    task = next(iter(repository.tasks.values()))
    assert task.status is TaskStatus.RUNNING
    assert queue.nak_failures == [
        DomainFailure("INGESTION_RETRYABLE", "model unavailable", retryable=True)
    ]

    assert await worker_once(queue, repository, ingestion, "worker-2", now, max_deliveries=2)
    job = await repository.get_job(submitted.job_id)
    task = next(iter(repository.tasks.values()))
    assert job is not None and job.status is JobStatus.FAILED
    assert job.retryable is True
    assert task.status is TaskStatus.FAILED
    assert queue.acked_task_ids == [task.id]
