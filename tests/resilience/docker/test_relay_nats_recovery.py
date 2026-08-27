"""真实容器下事务 Outbox 与 NATS 故障恢复验证。"""

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
    delete_document,
    submit_bytes,
    wait_for_job_status,
    wait_for_queue_drained,
    wait_until,
)


@pytest.mark.docker_resilience
@pytest.mark.asyncio
async def test_relay_kill_after_publish_before_mark_republishes_safely(
    rag_stub: Any,
    embedding_runtime: EmbeddingRuntime,
    barrier_control: BarrierControl,
    docker_control: DockerControl,
    database_probe: DatabaseProbe,
    es_client: AsyncElasticsearch,
) -> None:
    """Relay 在发布后标记前被强杀时，重复投递不得造成重复索引。"""
    barrier_control.prepare(Checkpoint.AFTER_RELAY_PUBLISH_BEFORE_MARK)
    dataset_id = await create_dataset(rag_stub, embedding_runtime, "relay-kill")
    document_id, job_id, _ = await submit_bytes(
        rag_stub,
        dataset_id,
        "relay-recovery.txt",
        b"The durable relay recovery phrase is copper horizon.",
    )
    await barrier_control.wait_reached(Checkpoint.AFTER_RELAY_PUBLISH_BEFORE_MARK)

    assert (
        await database_probe.scalar(
            "SELECT o.status FROM outbox_events o JOIN tasks t ON t.id = o.task_id "
            "WHERE t.job_id = :job_id",
            job_id=job_id,
        )
        == "READY_TO_PUBLISH"
    )

    docker_control.kill(docker_control.outbox)
    docker_control.start(docker_control.outbox)
    await wait_for_job_status(rag_stub, job_id, rag_service_pb2.JOB_STATUS_SUCCEEDED)
    await wait_for_queue_drained()

    async def outbox_is_published() -> bool:
        """轮询指定 Job 的 Outbox 是否已被 Relay 标记为已发布。"""
        status = await database_probe.scalar(
            "SELECT o.status FROM outbox_events o JOIN tasks t ON t.id = o.task_id "
            "WHERE t.job_id = :job_id",
            job_id=job_id,
        )
        return bool(status == "PUBLISHED")

    await wait_until(outbox_is_published, deadline_seconds=30)

    state = await database_probe.one(
        "SELECT o.status AS outbox_status, t.status AS task_status, t.attempt "
        "FROM outbox_events o JOIN tasks t ON t.id = o.task_id WHERE t.job_id = :job_id",
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

    assert state == {"outbox_status": "PUBLISHED", "task_status": "SUCCEEDED", "attempt": 1}
    assert manifest_count == es_count["count"] == 1


@pytest.mark.docker_resilience
@pytest.mark.asyncio
async def test_ready_outbox_survives_nats_stop_and_publishes_after_restart(
    rag_stub: Any,
    embedding_runtime: EmbeddingRuntime,
    barrier_control: BarrierControl,
    docker_control: DockerControl,
    database_probe: DatabaseProbe,
) -> None:
    """NATS 暂停期间 READY Outbox 必须保留，并在恢复后完成发布。"""
    barrier_control.prepare(None)
    dataset_id = await create_dataset(rag_stub, embedding_runtime, "nats-outage")
    document_id, ingestion_job_id, _ = await submit_bytes(
        rag_stub,
        dataset_id,
        "nats-outage.txt",
        b"The NATS restart recovery phrase is indigo compass.",
    )
    await wait_for_job_status(
        rag_stub,
        ingestion_job_id,
        rag_service_pb2.JOB_STATUS_SUCCEEDED,
    )
    await wait_for_queue_drained()

    docker_control.stop(docker_control.nats)
    deleted = await delete_document(rag_stub, document_id)
    job_id = str(deleted.job_id)

    async def outbox_is_ready() -> bool:
        """轮询删除 Job 的 Outbox 是否已进入可发布状态。"""
        status = await database_probe.scalar(
            "SELECT o.status FROM outbox_events o JOIN tasks t ON t.id = o.task_id "
            "WHERE t.job_id = :job_id",
            job_id=job_id,
        )
        return bool(status == "READY_TO_PUBLISH")

    await wait_until(outbox_is_ready, deadline_seconds=30)
    docker_control.start(docker_control.nats)
    docker_control.wait_healthy(docker_control.nats)
    docker_control.start(docker_control.outbox)
    docker_control.start(docker_control.worker)

    await wait_for_job_status(rag_stub, job_id, rag_service_pb2.JOB_STATUS_SUCCEEDED)
    await wait_for_queue_drained()
    assert (
        await database_probe.scalar(
            "SELECT o.status FROM outbox_events o JOIN tasks t ON t.id = o.task_id "
            "WHERE t.job_id = :job_id",
            job_id=job_id,
        )
        == "PUBLISHED"
    )
