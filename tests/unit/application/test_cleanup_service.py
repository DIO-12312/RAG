from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rag_mvp.application.cleanup_service import CleanupService
from rag_mvp.application.document_service import DocumentService
from rag_mvp.application.dto import (
    CreateDatasetCommand,
    DeleteDatasetCommand,
    SubmitDocumentCommand,
)
from rag_mvp.domain.enums import DatasetStatus, TaskStatus
from rag_mvp.outbox.finalizer import finalize_once
from tests.fakes.metadata import FakeMetadataRepository
from tests.fakes.search_engine import FakeSearchEngine
from tests.fakes.storage import FakeObjectStorage


class OrderedSearch(FakeSearchEngine):
    def __init__(self, calls: list[str], *, fail: bool = False) -> None:
        super().__init__()
        self._calls = calls
        self._fail = fail

    async def delete_dataset(self, dataset_id: str) -> None:
        self._calls.append(f"search:{dataset_id}")
        if self._fail:
            raise ConnectionError("search unavailable")
        await super().delete_dataset(dataset_id)


class OrderedStorage(FakeObjectStorage):
    def __init__(self, calls: list[str]) -> None:
        super().__init__()
        self._calls = calls

    async def delete(self, key: str) -> None:
        self._calls.append(f"storage:{key}")
        await super().delete(key)


async def _deleting_dataset(
    storage: FakeObjectStorage,
    now: datetime,
) -> tuple[FakeMetadataRepository, str, str]:
    repository = FakeMetadataRepository()
    documents = DocumentService(repository, storage, max_upload_bytes=1024)
    await documents.create_dataset(
        CreateDatasetCommand("create", "create", "Docs", "fake", 8, now, "dataset-1")
    )
    await documents.submit_document(
        SubmitDocumentCommand(
            "submit",
            "submit",
            "dataset-1",
            "guide.txt",
            b"disposable",
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
    deleted = await documents.delete_dataset(
        DeleteDatasetCommand("delete", "delete", "dataset-1", now)
    )
    task = next(task for task in repository.tasks.values() if task.job_id == deleted.job_id)
    return repository, deleted.job_id, task.id


@pytest.mark.asyncio
async def test_dataset_cleanup_deletes_search_then_objects_then_purges_metadata() -> None:
    now = datetime.now(UTC)
    calls: list[str] = []
    storage = OrderedStorage(calls)
    repository, job_id, task_id = await _deleting_dataset(storage, now)
    search = OrderedSearch(calls)

    result = await CleanupService(repository, search, storage).execute(task_id, 1, now)

    assert result.claimed is True and result.completed is True
    assert calls[0] == "search:dataset-1"
    assert all(call.startswith("storage:") for call in calls[1:])
    assert await repository.get_dataset("dataset-1") is None
    assert await repository.get_job(job_id) is None
    assert await repository.get_task(task_id) is None
    assert storage.objects == {}


@pytest.mark.asyncio
async def test_dataset_cleanup_failure_keeps_deleting_metadata_for_retry() -> None:
    now = datetime.now(UTC)
    calls: list[str] = []
    storage = OrderedStorage(calls)
    repository, job_id, task_id = await _deleting_dataset(storage, now)

    result = await CleanupService(repository, OrderedSearch(calls, fail=True), storage).execute(
        task_id, 1, now
    )

    dataset = await repository.get_dataset("dataset-1")
    task = await repository.get_task(task_id)
    assert result.failure is not None and result.failure.retryable is True
    assert dataset is not None and dataset.status is DatasetStatus.DELETING
    assert await repository.get_job(job_id) is not None
    assert task is not None and task.status is TaskStatus.RUNNING
    assert storage.objects
