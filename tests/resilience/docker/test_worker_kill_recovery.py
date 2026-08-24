"""Worker SIGKILL recovery at persisted indexing and completion boundaries."""

from __future__ import annotations

from typing import Any

import pytest
from elasticsearch import AsyncElasticsearch

from rag_mvp.ingestion.checkpoints import Checkpoint
from rag_mvp.rpc.generated import rag_service_pb2
from tests.resilience.docker.conftest import (
    BarrierControl,
    DatabaseProbe,
    DockerControl,
    EmbeddingRuntime,
    create_dataset,
    submit_bytes,
    wait_for_job_status,
    wait_for_queue_drained,
)


@pytest.mark.docker_resilience
@pytest.mark.asyncio
async def test_worker_kill_after_index_write_redelivers_without_duplicate_chunks(
    rag_stub: Any,
    embedding_runtime: EmbeddingRuntime,
    barrier_control: BarrierControl,
    docker_control: DockerControl,
    database_probe: DatabaseProbe,
    es_client: AsyncElasticsearch,
) -> None:
    barrier_control.prepare(Checkpoint.AFTER_INDEX_WRITE)
    dataset_id = await create_dataset(rag_stub, embedding_runtime, "after-index")
    document_id, job_id, reused = await submit_bytes(
        rag_stub,
        dataset_id,
        "worker-kill.txt",
        b"The Helios recovery marker is violet and uniquely identifies this document.",
    )
    assert reused is False
    await barrier_control.wait_reached(Checkpoint.AFTER_INDEX_WRITE)

    before = await database_probe.one(
        "SELECT j.status AS job_status, t.status AS task_status, t.attempt AS attempt "
        "FROM jobs j JOIN tasks t ON t.job_id = j.id WHERE j.id = :job_id",
        job_id=job_id,
    )
    assert before == {"job_status": "RUNNING", "task_status": "RUNNING", "attempt": 1}

    docker_control.kill(docker_control.worker)
    docker_control.start(docker_control.worker)
    result = await wait_for_job_status(
        rag_stub,
        job_id,
        rag_service_pb2.JOB_STATUS_SUCCEEDED,
    )
    await wait_for_queue_drained()

    after = await database_probe.one(
        "SELECT d.status AS document_status, d.active_version, j.status AS job_status, "
        "t.status AS task_status, t.attempt, t.last_delivery_sequence "
        "FROM documents d JOIN jobs j ON j.document_id = d.id "
        "JOIN tasks t ON t.job_id = j.id WHERE j.id = :job_id",
        job_id=job_id,
    )
    manifest_count = await database_probe.scalar(
        "SELECT COUNT(*) FROM chunk_manifests WHERE document_id = :document_id",
        document_id=document_id,
    )
    await es_client.indices.refresh(index="rag-chunks-v1")
    es_count = await es_client.count(
        index="rag-chunks-v1",
        query={"term": {"document_id": document_id}},
    )

    assert result.task_status == rag_service_pb2.TASK_STATUS_SUCCEEDED
    assert after["document_status"] == "READY"
    assert after["active_version"] == 1
    assert after["job_status"] == after["task_status"] == "SUCCEEDED"
    assert after["attempt"] == 2
    assert after["last_delivery_sequence"] >= 2
    assert manifest_count == es_count["count"] == 1


@pytest.mark.docker_resilience
@pytest.mark.asyncio
async def test_worker_kill_after_success_before_ack_redelivery_only_acks(
    rag_stub: Any,
    embedding_runtime: EmbeddingRuntime,
    barrier_control: BarrierControl,
    docker_control: DockerControl,
    database_probe: DatabaseProbe,
    es_client: AsyncElasticsearch,
) -> None:
    barrier_control.prepare(Checkpoint.AFTER_COMPLETE_BEFORE_ACK)
    dataset_id = await create_dataset(rag_stub, embedding_runtime, "after-complete")
    document_id, job_id, _ = await submit_bytes(
        rag_stub,
        dataset_id,
        "ack-loss.txt",
        b"The Selene acknowledgement token is amber and must be indexed only once.",
    )
    await barrier_control.wait_reached(Checkpoint.AFTER_COMPLETE_BEFORE_ACK)

    persisted = await database_probe.one(
        "SELECT j.status AS job_status, t.status AS task_status, t.attempt "
        "FROM jobs j JOIN tasks t ON t.job_id = j.id WHERE j.id = :job_id",
        job_id=job_id,
    )
    assert persisted == {"job_status": "SUCCEEDED", "task_status": "SUCCEEDED", "attempt": 1}

    docker_control.kill(docker_control.worker)
    docker_control.start(docker_control.worker)
    await wait_for_queue_drained()

    task = await database_probe.one(
        "SELECT status, attempt FROM tasks WHERE job_id = :job_id",
        job_id=job_id,
    )
    manifest_count = await database_probe.scalar(
        "SELECT COUNT(*) FROM chunk_manifests WHERE document_id = :document_id",
        document_id=document_id,
    )
    await es_client.indices.refresh(index="rag-chunks-v1")
    es_count = await es_client.count(
        index="rag-chunks-v1",
        query={"term": {"document_id": document_id}},
    )

    assert task == {"status": "SUCCEEDED", "attempt": 1}
    assert manifest_count == es_count["count"] == 1
