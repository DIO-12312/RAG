from __future__ import annotations

# 验证 staging 提升失败、重启恢复和 TTL 清理不会留下错误 READY 事件。
from datetime import UTC, datetime, timedelta

import pytest

from rag_mvp.application.document_service import DocumentService
from rag_mvp.application.dto import CreateDatasetCommand, SubmitDocumentCommand
from rag_mvp.domain.enums import FingerprintState, JobStatus, OutboxStatus, TaskStatus
from rag_mvp.outbox.finalizer import finalize_once
from rag_mvp.outbox.sweeper import sweep_staging_once
from tests.fakes.metadata import FakeMetadataRepository
from tests.fakes.storage import FakeObjectStorage


class FailingPromotionStorage(FakeObjectStorage):
    async def promote(self, staging_key: str, final_key: str) -> str:
        """持续模拟对象提升失败，用来驱动 Finalizer 的耗尽分支。"""
        del staging_key, final_key
        raise OSError("object store unavailable")


async def _waiting(
    storage: FakeObjectStorage,
) -> tuple[FakeMetadataRepository, str, datetime]:
    """创建带 WAITING Outbox 的上传聚合，供恢复测试复用。"""
    now = datetime.now(UTC)
    repository = FakeMetadataRepository()
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
            b"finalizer source",
            None,
            None,
            "text-v1",
            800,
            120,
            "fake",
            now,
        )
    )
    return repository, submitted.job_id, now


@pytest.mark.asyncio
@pytest.mark.resilience
async def test_finalizer_exhaustion_reaches_stable_failure_terminal() -> None:
    """Finalizer 重试耗尽后必须收敛为可解释且稳定的失败终态。"""
    storage = FailingPromotionStorage()
    repository, job_id, now = await _waiting(storage)

    assert await finalize_once(repository, storage, now, limit=10, max_finalize_attempts=2) == 0
    event = next(iter(repository.outbox.values()))
    assert event.attempt == 1 and event.status is OutboxStatus.WAITING_OBJECT

    assert await finalize_once(repository, storage, now, limit=10, max_finalize_attempts=2) == 0
    event = next(iter(repository.outbox.values()))
    job = await repository.get_job(job_id)
    task = await repository.get_task(event.task_id)
    fingerprint = next(iter(repository.fingerprints.values()))
    assert event.status is OutboxStatus.CANCELLED
    assert job is not None and job.status is JobStatus.FAILED
    assert job.error is not None and job.error.code == "OBJECT_FINALIZATION_FAILED"
    assert task is not None and task.status is TaskStatus.FAILED
    assert fingerprint.state is FingerprintState.RELEASED


@pytest.mark.asyncio
@pytest.mark.resilience
async def test_staging_sweeper_preserves_waiting_reference_and_deletes_orphan() -> None:
    """TTL 清扫保留被 WAITING Outbox 引用的对象，只删除孤儿 staging。"""
    now = datetime.now(UTC)
    storage = FakeObjectStorage()
    repository, _, _ = await _waiting(storage)
    referenced_key = next(iter(repository.outbox.values())).staging_key
    assert referenced_key is not None
    await storage.write("staging/orphan", b"orphan")
    storage.modified_at[referenced_key] = now - timedelta(days=2)
    storage.modified_at["staging/orphan"] = now - timedelta(days=2)

    deleted = await sweep_staging_once(repository, storage, older_than=now - timedelta(days=1))

    assert deleted == 1
    assert await storage.exists(referenced_key)
    assert not await storage.exists("staging/orphan")
