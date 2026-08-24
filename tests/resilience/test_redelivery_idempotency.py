from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rag_mvp.adapters.chunkers.recursive import RecursiveChunker
from rag_mvp.adapters.parsers.text import TextParser
from rag_mvp.application.document_service import DocumentService
from rag_mvp.application.dto import CreateDatasetCommand, SubmitDocumentCommand
from rag_mvp.application.ingestion_service import IngestionService
from rag_mvp.domain.enums import JobStatus
from rag_mvp.ingestion.checkpoints import Checkpoint, InjectedWorkerCrash
from rag_mvp.ingestion.pipeline import IngestionPipeline
from rag_mvp.ingestion.worker import worker_once
from rag_mvp.outbox.finalizer import finalize_once
from rag_mvp.outbox.relay import relay_once
from tests.fakes.metadata import FakeMetadataRepository
from tests.fakes.model import FakeModelGateway
from tests.fakes.search_engine import FakeSearchEngine
from tests.fakes.storage import FakeObjectStorage
from tests.fakes.task_queue import FakeTaskQueue


async def _harness() -> tuple[
    datetime,
    FakeMetadataRepository,
    FakeTaskQueue,
    FakeModelGateway,
    FakeSearchEngine,
    str,
    FakeObjectStorage,
]:
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
    return now, repository, queue, model, search, submitted.job_id, storage


@pytest.mark.asyncio
@pytest.mark.resilience
async def test_crash_after_index_write_redelivers_and_upserts_idempotently() -> None:
    now, repository, queue, model, search, job_id, storage = await _harness()
    should_crash = True

    async def failpoint(checkpoint: Checkpoint) -> None:
        nonlocal should_crash
        if checkpoint is Checkpoint.AFTER_INDEX_WRITE and should_crash:
            should_crash = False
            raise InjectedWorkerCrash(checkpoint)

    ingestion = IngestionService(
        repository,
        IngestionPipeline(
            storage,
            TextParser(),
            RecursiveChunker(800, 120),
            model,
            search,
            failpoint=failpoint,
        ),
    )

    with pytest.raises(InjectedWorkerCrash):
        await worker_once(queue, repository, ingestion, "worker-1", now)
    assert search.record_count == 1
    await queue.redeliver_unacked()
    assert await worker_once(queue, repository, ingestion, "worker-2", now) is True

    job = await repository.get_job(job_id)
    assert job is not None and job.status is JobStatus.SUCCEEDED
    assert search.record_count == 1
    assert search.upsert_calls == 2


@pytest.mark.asyncio
@pytest.mark.resilience
async def test_crash_after_success_before_ack_redelivery_only_acks() -> None:
    now, repository, queue, model, search, job_id, storage = await _harness()
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
    crashed = False

    async def after_complete() -> None:
        nonlocal crashed
        crashed = True
        raise InjectedWorkerCrash(Checkpoint.AFTER_COMPLETE_BEFORE_ACK)

    with pytest.raises(InjectedWorkerCrash):
        await worker_once(
            queue,
            repository,
            ingestion,
            "worker-1",
            now,
            after_complete=after_complete,
        )
    assert crashed is True
    embed_calls = model.embed_calls
    await queue.redeliver_unacked()
    assert await worker_once(queue, repository, ingestion, "worker-2", now) is True

    job = await repository.get_job(job_id)
    assert job is not None and job.status is JobStatus.SUCCEEDED
    assert model.embed_calls == embed_calls
    assert search.upsert_calls == 1
