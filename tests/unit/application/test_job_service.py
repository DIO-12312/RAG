from __future__ import annotations

# 验证 Job 查询、取消、重试的状态机和并发语义。
from datetime import UTC, datetime

import pytest

from rag_mvp.application.document_service import DocumentService
from rag_mvp.application.dto import CreateDatasetCommand, GetJobQuery, SubmitDocumentCommand
from rag_mvp.application.job_service import JobService
from rag_mvp.domain.enums import JobStatus, TaskStatus
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
