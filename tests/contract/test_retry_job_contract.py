from __future__ import annotations

# 校验失败任务重试始终创建新 Job/Task 而非回写终态。
from datetime import UTC, datetime

import pytest

from rag_mvp.application.document_service import DocumentService
from rag_mvp.application.dto import CreateDatasetCommand, SubmitDocumentCommand
from rag_mvp.domain.enums import FingerprintState, JobStatus, OutboxStatus, TaskStatus
from rag_mvp.domain.errors import DomainError, DomainFailure
from rag_mvp.outbox.finalizer import finalize_once
from rag_mvp.ports.metadata import RetryJobRequest
from tests.fakes.metadata import FakeMetadataRepository
from tests.fakes.storage import FakeObjectStorage


async def _failed_ingestion(*, object_ready: bool) -> tuple[FakeMetadataRepository, str, datetime]:
    """构造本测试所需的输入、替身或运行环境。"""
    now = datetime.now(UTC)
    repository = FakeMetadataRepository()
    storage = FakeObjectStorage()
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
            b"retry source",
            None,
            None,
            "text-v1",
            800,
            120,
            "fake",
            now,
        )
    )
    if object_ready:
        await finalize_once(repository, storage, now, limit=10)
        ready = (await repository.list_ready_outbox(1))[0]
        await repository.mark_outbox_published(ready.id, now)
    task = await repository.get_task_for_job(submitted.job_id)
    assert task is not None
    await repository.claim_task(task.id, 1, now)
    await repository.fail_task(
        task.id,
        DomainFailure("MODEL_UNAVAILABLE", "temporary", retryable=True),
        now,
    )
    return repository, submitted.job_id, now


@pytest.mark.asyncio
async def test_retry_creates_new_job_task_and_ready_outbox_without_reviving_original() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    repository, original_job_id, now = await _failed_ingestion(object_ready=True)
    request = RetryJobRequest("retry-key", original_job_id, now, max_user_retries=3)

    first = await repository.retry_job(request)
    repeated = await repository.retry_job(request)
    concurrent_equivalent = await repository.retry_job(
        RetryJobRequest("other-retry-key", original_job_id, now, max_user_retries=3)
    )

    original = await repository.get_job(original_job_id)
    child = await repository.get_job(first.job_id)
    task = await repository.get_task(first.task_id)
    ready = await repository.list_ready_outbox(10)
    fingerprint = next(iter(repository.fingerprints.values()))
    assert original is not None and original.status is JobStatus.FAILED
    assert original.retry_count == 1
    assert child is not None and child.status is JobStatus.PENDING
    assert child.retry_of_job_id == original_job_id
    assert task is not None and task.status is TaskStatus.PENDING
    assert [event.status for event in ready] == [OutboxStatus.READY_TO_PUBLISH]
    assert ready[0].task_id == first.task_id
    assert repeated.job_id == first.job_id
    assert repeated.task_id == first.task_id
    assert repeated.reused is True
    assert concurrent_equivalent.job_id == first.job_id
    assert concurrent_equivalent.reused is True
    assert fingerprint.state is FingerprintState.PENDING
    assert fingerprint.job_id == child.id


@pytest.mark.asyncio
async def test_retry_rejects_failure_without_final_object() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    repository, original_job_id, now = await _failed_ingestion(object_ready=False)

    with pytest.raises(DomainError) as error:
        await repository.retry_job(
            RetryJobRequest("retry-key", original_job_id, now, max_user_retries=3)
        )

    assert error.value.failure.code == "RETRY_OBJECT_MISSING"
    assert next(iter(repository.fingerprints.values())).state is FingerprintState.RELEASED


@pytest.mark.asyncio
async def test_retry_enforces_user_retry_limit() -> None:
    """验证本测试场景的预期行为与边界条件。"""
    repository, original_job_id, now = await _failed_ingestion(object_ready=True)

    with pytest.raises(DomainError) as error:
        await repository.retry_job(
            RetryJobRequest("retry-key", original_job_id, now, max_user_retries=0)
        )

    assert error.value.failure.code == "MAX_USER_RETRIES_EXCEEDED"
