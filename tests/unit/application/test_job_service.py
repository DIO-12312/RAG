from __future__ import annotations

# 验证 Job 查询、取消、重试的状态机和并发语义。
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from rag_mvp.application.document_service import DocumentService
from rag_mvp.application.dto import (
    CreateDatasetCommand,
    GetJobQuery,
    ReindexDocumentCommand,
    SubmitDocumentCommand,
)
from rag_mvp.application.job_service import JobService
from rag_mvp.domain.enums import DocumentStatus, JobStatus, OutboxStatus, TaskStatus
from rag_mvp.domain.errors import DomainError
from tests.fakes.metadata import FakeMetadataRepository
from tests.fakes.storage import FakeObjectStorage


@pytest.mark.asyncio
async def test_job_service_returns_job_and_task_snapshot() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    now = datetime.now(UTC)
    repository = FakeMetadataRepository()
    documents = DocumentService(repository, FakeObjectStorage(), max_upload_bytes=1024)
    await documents.create_dataset(
        CreateDatasetCommand("request", "create", "Docs", "fake", 8, now, "dataset-1")
    )
    submitted = await documents.submit_document(
        SubmitDocumentCommand(
            "request",
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

    result = await JobService(repository).get_job(GetJobQuery("request", submitted.job_id))

    assert result.job_id == submitted.job_id
    assert result.status is JobStatus.PENDING
    assert result.task_status is TaskStatus.PENDING


@pytest.mark.asyncio
async def test_job_service_returns_stable_not_found_failure() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    with pytest.raises(DomainError) as error:
        await JobService(FakeMetadataRepository()).get_job(GetJobQuery("request", "missing"))

    assert error.value.failure.code == "JOB_NOT_FOUND"


@pytest.mark.asyncio
async def test_job_service_reindexes_an_already_indexed_document() -> None:
    """成功文档可复用正式对象创建新版本，旧 active_version 在完成前保持不变。"""

    now = datetime.now(UTC)
    repository = FakeMetadataRepository()
    documents = DocumentService(repository, FakeObjectStorage(), max_upload_bytes=1024)
    await documents.create_dataset(
        CreateDatasetCommand("request", "create", "Docs", "fake", 8, now, "dataset-1")
    )
    submitted = await documents.submit_document(
        SubmitDocumentCommand(
            "request",
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
    original = repository.jobs[submitted.job_id]
    document = repository.documents[submitted.document_id]
    repository.jobs[original.id] = replace(original, status=JobStatus.SUCCEEDED, progress=1.0)
    repository.documents[document.id] = replace(
        document,
        status=DocumentStatus.READY,
        active_version=1,
        object_key="objects/guide.txt",
    )

    result = await JobService(repository).reindex_document(
        ReindexDocumentCommand(
            "request", "reindex", document.id, "source-router-v11", 800, 120, now
        )
    )

    rebuilt = repository.jobs[result.job_id]
    current = repository.documents[document.id]
    task = await repository.get_task_for_job(result.job_id)
    assert task is not None
    assert result.status is JobStatus.PENDING
    assert rebuilt.index_version == 2
    assert rebuilt.config_digest != original.config_digest
    assert current.active_version == 1
    assert current.next_index_version == 3
    assert any(
        event.task_id == task.id and event.status is OutboxStatus.READY_TO_PUBLISH
        for event in repository.outbox.values()
    )
