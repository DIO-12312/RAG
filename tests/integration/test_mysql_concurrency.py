"""针对真实 MySQL 的重试、重建和删除围栏并发测试。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from rag_mvp.adapters.metadata.mysql import MySQLMetadataRepository
from rag_mvp.domain.enums import DocumentStatus, JobStatus, TaskStatus
from rag_mvp.domain.errors import DomainError, DomainFailure
from rag_mvp.domain.models import Chunk, Dataset, Locator
from rag_mvp.ports.metadata import (
    DeleteDatasetRequest,
    DeleteDocumentRequest,
    RetryJobRequest,
    SubmitIngestion,
    SubmitResult,
)


async def _submitted(
    repository: MySQLMetadataRepository,
    now: datetime,
) -> SubmitResult:
    """创建一份真实 MySQL 摄取提交，供并发场景复用。"""
    await repository.create_dataset(
        Dataset(
            id="dataset-1",
            tenant_id="default_tenant",
            name="Docs",
            embedding_model="fake-embedding",
            embedding_dimension=8,
            created_at=now,
        )
    )
    return await repository.submit_ingestion(
        SubmitIngestion(
            idempotency_key="submit",
            dataset_id="dataset-1",
            source_name="guide.txt",
            staging_key="staging/submit",
            file_sha256="a" * 64,
            config_digest="b" * 64,
            now=now,
        )
    )


def _chunk(document_id: str, index_version: int = 1) -> Chunk:
    """构造指定版本的最小持久化 Chunk。"""
    return Chunk(
        id="c" * 16,
        document_id=document_id,
        index_version=index_version,
        ordinal=0,
        content_with_weight=f"version {index_version} evidence",
        content_sha256="d" * 64,
        source_name="guide.txt",
        locator=Locator(start_line=1, end_line=1),
    )


async def _ready_and_claim(
    repository: MySQLMetadataRepository,
    submitted: SubmitResult,
    now: datetime,
) -> None:
    """将等待态 Outbox 置就绪并认领摄取任务。"""
    event = (await repository.list_waiting_outbox(1))[0]
    assert await repository.mark_object_ready(event.id, "objects/document/source", now)
    assert await repository.claim_task(submitted.task_id, 1, now)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_retry_keys_reuse_one_active_child_job(
    mysql_repository: tuple[MySQLMetadataRepository, AsyncEngine],
) -> None:
    """并发重试键只能复用同一个活跃子 Job。"""
    repository, engine = mysql_repository
    now = datetime.now(UTC)
    submitted = await _submitted(repository, now)
    await _ready_and_claim(repository, submitted, now)
    assert await repository.fail_task(
        submitted.task_id,
        DomainFailure("MODEL_UNAVAILABLE", "temporary", retryable=True),
        now,
    )

    children = await asyncio.gather(
        *(
            repository.retry_job(
                RetryJobRequest(f"retry-{index}", submitted.job_id, now, max_user_retries=3)
            )
            for index in range(8)
        )
    )

    assert len({child.job_id for child in children}) == 1
    assert len({child.task_id for child in children}) == 1
    child = await repository.get_job(children[0].job_id)
    child_task = await repository.get_task(children[0].task_id)
    original = await repository.get_job(submitted.job_id)
    assert original is not None and original.status is JobStatus.FAILED
    assert original.retry_count == 1
    assert child is not None and child.status is JobStatus.PENDING
    assert child.retry_of_job_id == original.id
    assert child_task is not None and child_task.status is TaskStatus.PENDING
    async with engine.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT COUNT(*) FROM jobs WHERE retry_of_job_id = :job_id"),
                {"job_id": submitted.job_id},
            )
            == 1
        )
        assert (
            await connection.scalar(text("SELECT status FROM index_builds LIMIT 1")) == "BUILDING"
        )
        assert (
            await connection.scalar(
                text("SELECT COUNT(*) FROM idempotency_records WHERE operation_type = 'RETRY_JOB'")
            )
            == 8
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_rebuilds_allocate_distinct_index_versions(
    mysql_repository: tuple[MySQLMetadataRepository, AsyncEngine],
) -> None:
    """并发重建必须分配不同且连续的索引版本。"""
    repository, _engine = mysql_repository
    now = datetime.now(UTC)
    submitted = await _submitted(repository, now)
    await _ready_and_claim(repository, submitted, now)
    assert await repository.complete_ingestion(
        submitted.task_id,
        [_chunk(submitted.document_id)],
        now,
    )

    rebuilds = await asyncio.gather(
        *(
            repository.submit_ingestion(
                SubmitIngestion(
                    idempotency_key=f"rebuild-{index}",
                    dataset_id="dataset-1",
                    source_name=f"guide-{index}.txt",
                    staging_key=f"staging/rebuild-{index}",
                    file_sha256=f"{index}" * 64,
                    config_digest="e" * 64,
                    now=now,
                    target_document_id=submitted.document_id,
                )
            )
            for index in range(1, 5)
        )
    )
    jobs = [await repository.get_job(result.job_id) for result in rebuilds]

    assert all(job is not None for job in jobs)
    assert {job.index_version for job in jobs if job is not None} == {2, 3, 4, 5}
    document = await repository.get_document(submitted.document_id)
    assert document is not None and document.active_version == 1
    assert document.next_index_version == 6
    assert await repository.visible_document_versions([document.id]) == {document.id: 1}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_out_of_order_rebuild_completion_never_regresses_active_version(
    mysql_repository: tuple[MySQLMetadataRepository, AsyncEngine],
) -> None:
    """较旧版本后完成时不得回退 active version。"""
    repository, engine = mysql_repository
    now = datetime.now(UTC)
    submitted = await _submitted(repository, now)
    await _ready_and_claim(repository, submitted, now)
    assert await repository.complete_ingestion(
        submitted.task_id,
        [_chunk(submitted.document_id)],
        now,
    )
    version_two, version_three = await asyncio.gather(
        repository.submit_ingestion(
            SubmitIngestion(
                idempotency_key="rebuild-two",
                dataset_id="dataset-1",
                source_name="guide-two.txt",
                staging_key="staging/rebuild-two",
                file_sha256="2" * 64,
                config_digest="e" * 64,
                now=now,
                target_document_id=submitted.document_id,
            )
        ),
        repository.submit_ingestion(
            SubmitIngestion(
                idempotency_key="rebuild-three",
                dataset_id="dataset-1",
                source_name="guide-three.txt",
                staging_key="staging/rebuild-three",
                file_sha256="3" * 64,
                config_digest="e" * 64,
                now=now,
                target_document_id=submitted.document_id,
            )
        ),
    )
    jobs = {
        (await repository.get_job(result.job_id)).index_version: result
        for result in (version_two, version_three)
    }
    assert await repository.claim_task(jobs[3].task_id, 3, now)
    assert await repository.complete_ingestion(
        jobs[3].task_id,
        [_chunk(submitted.document_id, 3)],
        now,
    )
    assert await repository.claim_task(jobs[2].task_id, 2, now)
    assert await repository.complete_ingestion(
        jobs[2].task_id,
        [_chunk(submitted.document_id, 2)],
        now,
    )

    document = await repository.get_document(submitted.document_id)
    assert document is not None and document.active_version == 3
    async with engine.connect() as connection:
        builds = dict(
            (
                await connection.execute(
                    text(
                        "SELECT index_version, status FROM index_builds "
                        "WHERE document_id = :document_id ORDER BY index_version"
                    ),
                    {"document_id": submitted.document_id},
                )
            ).all()
        )
        cleanup_count = await connection.scalar(
            text(
                "SELECT COUNT(*) FROM jobs WHERE document_id = :document_id "
                "AND type = 'CLEANUP_INDEX_VERSION' AND index_version = 2"
            ),
            {"document_id": submitted.document_id},
        )
    assert builds == {1: "ACTIVE", 2: "ABANDONED", 3: "ACTIVE"}
    assert cleanup_count == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_delete_and_finalizer_race_never_leaves_ingest_outbox_ready(
    mysql_repository: tuple[MySQLMetadataRepository, AsyncEngine],
) -> None:
    """删除与 Finalizer 竞态不能遗留可发布的摄取 Outbox。"""
    repository, engine = mysql_repository
    now = datetime.now(UTC)
    submitted = await _submitted(repository, now)
    ingest_event = (await repository.list_waiting_outbox(1))[0]

    ready_result, deleted = await asyncio.gather(
        repository.mark_object_ready(ingest_event.id, "objects/document/source", now),
        repository.delete_document(DeleteDocumentRequest("delete-key", submitted.document_id, now)),
    )

    document = await repository.get_document(submitted.document_id)
    assert isinstance(ready_result, bool)
    assert document is not None and document.status is DocumentStatus.DELETED
    assert await repository.visible_document_versions([document.id]) == {}
    cleanup_task = await repository.get_task(deleted.task_id)
    assert cleanup_task is not None and cleanup_task.status is TaskStatus.PENDING
    async with engine.connect() as connection:
        rows = await connection.execute(
            text(
                "SELECT tasks.type, outbox_events.status "
                "FROM outbox_events JOIN tasks ON tasks.id = outbox_events.task_id "
                "ORDER BY tasks.type"
            )
        )
        statuses = {str(task_type): str(status) for task_type, status in rows}
    assert statuses == {
        "CLEANUP_DOCUMENT": "READY_TO_PUBLISH",
        "INGEST_DOCUMENT": "CANCELLED",
    }


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_dataset_delete_keys_create_one_cleanup_job(
    mysql_repository: tuple[MySQLMetadataRepository, AsyncEngine],
) -> None:
    """并发数据集删除只允许创建一个清理 Job。"""
    repository, engine = mysql_repository
    now = datetime.now(UTC)
    await _submitted(repository, now)

    results = await asyncio.gather(
        *(
            repository.delete_dataset(
                DeleteDatasetRequest(f"delete-dataset-{index}", "dataset-1", now)
            )
            for index in range(8)
        ),
        return_exceptions=True,
    )

    successes = [result for result in results if not isinstance(result, Exception)]
    failures = [result for result in results if isinstance(result, DomainError)]
    assert len(successes) == 1
    assert len(failures) == 7
    assert {failure.failure.code for failure in failures} == {"DATASET_DELETION_IN_PROGRESS"}
    async with engine.connect() as connection:
        assert (
            await connection.scalar(text("SELECT COUNT(*) FROM jobs WHERE type = 'DELETE_DATASET'"))
            == 1
        )
