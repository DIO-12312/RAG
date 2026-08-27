"""真实 MySQL/ES/NATS 环境中的并发与生命周期围栏场景。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from elasticsearch import AsyncElasticsearch

from rag_mvp.adapters.metadata.mysql import MySQLMetadataRepository
from rag_mvp.domain.errors import DomainFailure
from rag_mvp.ingestion.checkpoints import Checkpoint
from rag_mvp.rpc.generated import rag_service_pb2
from tests.resilience.docker.conftest import (
    BarrierControl,
    DatabaseProbe,
    DockerControl,
    EmbeddingRuntime,
    create_dataset,
    delete_document,
    grpc_result,
    retry_job,
    submit_bytes,
    unique_id,
    wait_for_job_status,
    wait_for_queue_drained,
)


@pytest.mark.docker_resilience
@pytest.mark.asyncio
async def test_concurrent_same_content_upload_reuses_one_canonical_job(
    rag_stub: Any,
    embedding_runtime: EmbeddingRuntime,
    barrier_control: BarrierControl,
    database_probe: DatabaseProbe,
) -> None:
    """并发同内容上传应锁定同一指纹，并仅创建一个规范 Job。"""
    barrier_control.prepare(None)
    dataset_id = await create_dataset(rag_stub, embedding_runtime, "upload-race")
    results = await asyncio.gather(
        *(
            submit_bytes(
                rag_stub,
                dataset_id,
                "same-content.txt",
                b"The canonical upload fingerprint phrase is silver kestrel.",
            )
            for _ in range(8)
        )
    )
    document_ids = {document_id for document_id, _, _ in results}
    job_ids = {job_id for _, job_id, _ in results}
    assert len(document_ids) == len(job_ids) == 1
    assert sum(not reused for _, _, reused in results) == 1

    job_id = next(iter(job_ids))
    await wait_for_job_status(rag_stub, job_id, rag_service_pb2.JOB_STATUS_SUCCEEDED)
    facts = await database_probe.one(
        "SELECT COUNT(DISTINCT f.id) AS fingerprints, COUNT(DISTINCT j.id) AS jobs, "
        "COUNT(DISTINCT t.id) AS tasks, COUNT(DISTINCT o.id) AS outbox_events "
        "FROM ingestion_fingerprints f JOIN jobs j ON j.id = f.job_id "
        "JOIN tasks t ON t.job_id = j.id JOIN outbox_events o ON o.task_id = t.id "
        "WHERE f.dataset_id = :dataset_id",
        dataset_id=dataset_id,
    )
    assert facts == {"fingerprints": 1, "jobs": 1, "tasks": 1, "outbox_events": 1}


@pytest.mark.docker_resilience
@pytest.mark.asyncio
async def test_concurrent_retry_calls_create_one_child_and_one_delivery(
    rag_stub: Any,
    embedding_runtime: EmbeddingRuntime,
    barrier_control: BarrierControl,
    docker_control: DockerControl,
    database_probe: DatabaseProbe,
    metadata_repository: MySQLMetadataRepository,
) -> None:
    """多个 RetryJob 并发请求只能创建一个子 Job 与一次有效投递。"""
    barrier_control.prepare(Checkpoint.AFTER_INDEX_WRITE)
    dataset_id = await create_dataset(rag_stub, embedding_runtime, "retry-race")
    _, original_job_id, _ = await submit_bytes(
        rag_stub,
        dataset_id,
        "retry-race.txt",
        b"The retry race marker is crimson lattice.",
    )
    await barrier_control.wait_reached(Checkpoint.AFTER_INDEX_WRITE)
    docker_control.kill(docker_control.worker)
    task_id = await database_probe.scalar(
        "SELECT id FROM tasks WHERE job_id = :job_id",
        job_id=original_job_id,
    )
    assert await metadata_repository.fail_task(
        str(task_id),
        DomainFailure("TEST_RETRYABLE", "controlled retry setup", retryable=True),
        datetime.now(UTC),
    )

    retried = await asyncio.gather(*(retry_job(rag_stub, original_job_id) for _ in range(8)))
    child_ids = {str(result.job_id) for result in retried}
    assert len(child_ids) == 1
    child_job_id = next(iter(child_ids))

    facts = await database_probe.one(
        "SELECT (SELECT retry_count FROM jobs WHERE id = :original_job_id) AS retry_count, "
        "COUNT(*) AS children FROM jobs WHERE retry_of_job_id = :original_job_id",
        original_job_id=original_job_id,
    )
    assert facts == {"retry_count": 1, "children": 1}

    docker_control.start(docker_control.worker)
    await wait_for_job_status(rag_stub, child_job_id, rag_service_pb2.JOB_STATUS_SUCCEEDED)
    await wait_for_queue_drained()


@pytest.mark.docker_resilience
@pytest.mark.asyncio
async def test_concurrent_rebuilds_allocate_unique_monotonic_versions(
    rag_stub: Any,
    embedding_runtime: EmbeddingRuntime,
    barrier_control: BarrierControl,
    docker_control: DockerControl,
    database_probe: DatabaseProbe,
) -> None:
    """并发重建同一文档时，索引版本必须唯一且单调递增。"""
    barrier_control.prepare(None)
    dataset_id = await create_dataset(rag_stub, embedding_runtime, "rebuild-race")
    document_id, initial_job_id, _ = await submit_bytes(
        rag_stub,
        dataset_id,
        "rebuild.txt",
        b"Initial rebuild content uses marker version one.",
    )
    await wait_for_job_status(rag_stub, initial_job_id, rag_service_pb2.JOB_STATUS_SUCCEEDED)
    docker_control.stop(docker_control.worker)

    rebuilds = await asyncio.gather(
        submit_bytes(
            rag_stub,
            dataset_id,
            "rebuild.txt",
            b"Concurrent rebuild alpha uses marker version two.",
            target_document_id=document_id,
        ),
        submit_bytes(
            rag_stub,
            dataset_id,
            "rebuild.txt",
            b"Concurrent rebuild beta uses marker version three.",
            target_document_id=document_id,
        ),
    )
    rebuild_job_ids = [job_id for _, job_id, _ in rebuilds]
    versions = {
        int(
            await database_probe.scalar(
                "SELECT index_version FROM jobs WHERE id = :job_id",
                job_id=job_id,
            )
        )
        for job_id in rebuild_job_ids
    }
    assert versions == {2, 3}

    docker_control.start(docker_control.worker)
    await asyncio.gather(
        *(
            wait_for_job_status(rag_stub, job_id, rag_service_pb2.JOB_STATUS_SUCCEEDED)
            for job_id in rebuild_job_ids
        )
    )
    await wait_for_queue_drained()
    assert await database_probe.scalar(
        "SELECT active_version FROM documents WHERE id = :document_id",
        document_id=document_id,
    ) == max(versions)


@pytest.mark.docker_resilience
@pytest.mark.asyncio
async def test_delete_during_blocked_rebuild_never_resurrects_document(
    rag_stub: Any,
    embedding_runtime: EmbeddingRuntime,
    barrier_control: BarrierControl,
    docker_control: DockerControl,
    database_probe: DatabaseProbe,
    es_client: AsyncElasticsearch,
) -> None:
    """删除发生在重建检查点时，恢复后的 Worker 不得复活文档。"""
    barrier_control.prepare(None)
    dataset_id = await create_dataset(rag_stub, embedding_runtime, "delete-fence")
    document_id, initial_job_id, _ = await submit_bytes(
        rag_stub,
        dataset_id,
        "delete-fence.txt",
        b"The original lifecycle marker is polar cedar.",
    )
    await wait_for_job_status(rag_stub, initial_job_id, rag_service_pb2.JOB_STATUS_SUCCEEDED)

    barrier_control.prepare(Checkpoint.AFTER_INDEX_WRITE)
    _, rebuild_job_id, _ = await submit_bytes(
        rag_stub,
        dataset_id,
        "delete-fence.txt",
        b"The blocked rebuild marker is polar cedar generation two.",
        target_document_id=document_id,
    )
    await barrier_control.wait_reached(Checkpoint.AFTER_INDEX_WRITE)
    deleted = await delete_document(rag_stub, document_id)

    invisible = grpc_result(
        await rag_stub.Retrieve(
            rag_service_pb2.RetrieveRequest(
                request_id=unique_id("retrieve-deleted"),
                dataset_id=dataset_id,
                query="polar cedar",
                top_k=6,
                max_context_tokens=4000,
            ),
            timeout=30,
        )
    )
    assert list(invisible.evidence) == []

    docker_control.kill(docker_control.worker)
    docker_control.start(docker_control.worker)
    await wait_for_job_status(
        rag_stub,
        str(deleted.job_id),
        rag_service_pb2.JOB_STATUS_SUCCEEDED,
    )
    await wait_for_queue_drained()

    facts = await database_probe.one(
        "SELECT d.status, d.lifecycle_generation, d.object_key, j.status AS rebuild_status "
        "FROM documents d JOIN jobs j ON j.document_id = d.id "
        "WHERE d.id = :document_id AND j.id = :rebuild_job_id",
        document_id=document_id,
        rebuild_job_id=rebuild_job_id,
    )
    await es_client.indices.refresh(index="rag-chunks-v1")
    es_count = await es_client.count(
        index="rag-chunks-v1",
        query={"term": {"document_id": document_id}},
    )
    assert facts["status"] == "DELETED"
    assert facts["lifecycle_generation"] >= 1
    assert facts["object_key"] is None
    assert facts["rebuild_status"] == "CANCELLED"
    assert es_count["count"] == 0
