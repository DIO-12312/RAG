from __future__ import annotations

# 校验删除文档 RPC、领域服务与异步清理的契约边界。
from datetime import UTC, datetime

import pytest

from rag_mvp.application.document_service import DocumentService
from rag_mvp.application.dto import CreateDatasetCommand, SubmitDocumentCommand
from rag_mvp.domain.enums import (
    DocumentStatus,
    FingerprintState,
    JobStatus,
    OutboxStatus,
    TaskStatus,
)
from rag_mvp.domain.errors import DomainError
from rag_mvp.ports.metadata import DeleteDocumentRequest
from tests.fakes.metadata import FakeMetadataRepository
from tests.fakes.storage import FakeObjectStorage


@pytest.mark.asyncio
async def test_delete_atomically_hides_document_cancels_ingest_and_creates_cleanup() -> None:
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
            b"delete source",
            None,
            None,
            "text-v1",
            800,
            120,
            "fake",
            now,
        )
    )

    deleted = await repository.delete_document(
        DeleteDocumentRequest("delete-key", submitted.document_id, now)
    )
    repeated = await repository.delete_document(
        DeleteDocumentRequest("delete-key", submitted.document_id, now)
    )

    document = await repository.get_document(submitted.document_id)
    ingest_task = await repository.get_task_for_job(submitted.job_id)
    cleanup_job = await repository.get_job(deleted.job_id)
    cleanup_task = await repository.get_task(deleted.task_id)
    assert document is not None and document.status is DocumentStatus.DELETED
    assert document.lifecycle_generation == 1
    assert await repository.visible_document_versions([document.id]) == {}
    assert ingest_task is not None and ingest_task.status is TaskStatus.CANCELLED
    assert cleanup_job is not None and cleanup_job.status is JobStatus.PENDING
    assert cleanup_task is not None and cleanup_task.status is TaskStatus.PENDING
    assert repeated.job_id == deleted.job_id and repeated.reused is True
    assert next(iter(repository.fingerprints.values())).state is FingerprintState.RELEASED
    assert {event.status for event in repository.outbox.values()} == {
        OutboxStatus.CANCELLED,
        OutboxStatus.READY_TO_PUBLISH,
    }


@pytest.mark.asyncio
async def test_new_delete_request_for_deleted_document_is_rejected() -> None:
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
            b"delete source",
            None,
            None,
            "text-v1",
            800,
            120,
            "fake",
            now,
        )
    )
    await repository.delete_document(DeleteDocumentRequest("delete-1", submitted.document_id, now))

    with pytest.raises(DomainError) as error:
        await repository.delete_document(
            DeleteDocumentRequest("delete-2", submitted.document_id, now)
        )

    assert error.value.failure.code == "DOCUMENT_ALREADY_DELETED"
