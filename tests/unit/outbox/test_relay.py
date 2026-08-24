from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rag_mvp.application.document_service import DocumentService
from rag_mvp.application.dto import CreateDatasetCommand, SubmitDocumentCommand
from rag_mvp.outbox.finalizer import finalize_once
from rag_mvp.outbox.relay import relay_once
from tests.fakes.metadata import FakeMetadataRepository
from tests.fakes.storage import FakeObjectStorage
from tests.fakes.task_queue import FakeTaskQueue


async def _ready_work() -> tuple[FakeMetadataRepository, FakeTaskQueue]:
    now = datetime.now(UTC)
    repository = FakeMetadataRepository()
    storage = FakeObjectStorage()
    queue = FakeTaskQueue()
    service = DocumentService(repository, storage, max_upload_bytes=1024)
    await service.create_dataset(
        CreateDatasetCommand("trace", "create", "Docs", "fake", 8, now, "dataset-1")
    )
    await service.submit_document(
        SubmitDocumentCommand(
            "trace",
            "submit",
            "dataset-1",
            "guide.txt",
            b"hello",
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
    return repository, queue


@pytest.mark.asyncio
async def test_relay_only_publishes_ready_outbox() -> None:
    repository = FakeMetadataRepository()
    queue = FakeTaskQueue()

    assert await relay_once(repository, queue, datetime.now(UTC), limit=10) == 0
    assert await queue.consume("worker", 0) is None


@pytest.mark.asyncio
async def test_publish_then_crash_before_mark_is_safely_retried() -> None:
    repository, queue = await _ready_work()
    crashed = False

    async def crash_after_publish() -> None:
        nonlocal crashed
        crashed = True
        raise RuntimeError("process crashed after publish")

    with pytest.raises(RuntimeError, match="crashed"):
        await relay_once(
            repository,
            queue,
            datetime.now(UTC),
            limit=10,
            after_publish=crash_after_publish,
        )
    assert crashed is True
    assert len(await repository.list_ready_outbox(limit=10)) == 1

    assert await relay_once(repository, queue, datetime.now(UTC), limit=10) == 1
    first = await queue.consume("worker", 0)
    second = await queue.consume("worker", 0)
    assert first is not None and second is not None
    assert first.task_id == second.task_id
    assert first.delivery_sequence != second.delivery_sequence
