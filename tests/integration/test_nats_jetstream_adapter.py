"""针对真实 NATS JetStream 的 adapter 集成测试。"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import suppress

import nats
import pytest
import pytest_asyncio
from nats.js.errors import NotFoundError

from rag_mvp.adapters.message_queue.nats_jetstream import NatsJetStreamTaskQueue
from rag_mvp.domain.errors import DomainError, DomainFailure


@pytest_asyncio.fixture
async def nats_queue() -> AsyncIterator[tuple[NatsJetStreamTaskQueue, str, str, str, str]]:
    """创建独占 Stream/Consumer 的 JetStream 队列，避免真实 NATS 测试串扰。"""
    url = os.environ.get("RAG_TEST_NATS_URL", "nats://127.0.0.1:4222")
    suffix = uuid.uuid4().hex
    stream = f"RAGTEST_{suffix}"
    subject = f"rag.test.{suffix}"
    consumer = f"worker_{suffix}"
    queue: NatsJetStreamTaskQueue | None = None
    try:
        queue = await NatsJetStreamTaskQueue.connect(
            url,
            stream,
            subject,
            consumer,
            ack_wait_seconds=0.3,
            max_deliver=3,
        )
        yield queue, url, stream, subject, consumer
    finally:
        if queue is not None:
            await queue.close()
        admin = await nats.connect(url)
        try:
            with suppress(NotFoundError):
                await admin.jetstream().delete_stream(stream)
        finally:
            await admin.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_jetstream_preserves_duplicate_publish_and_ack_removes_deliveries(
    nats_queue: tuple[NatsJetStreamTaskQueue, str, str, str, str],
) -> None:
    """验证重复发布保留消息，而 ACK 会移除对应 delivery。"""
    queue, _url, _stream, _subject, _consumer = nats_queue
    await queue.publish("task-1")
    await queue.publish("task-1")

    first = await queue.consume("worker-a", timeout_seconds=1)
    second = await queue.consume("worker-a", timeout_seconds=1)
    assert first is not None and second is not None
    assert first.task_id == second.task_id == "task-1"
    assert first.delivery_sequence != second.delivery_sequence
    assert first.redelivery_count == second.redelivery_count == 0

    await queue.ack(first)
    await queue.ack(second)
    assert await queue.consume("worker-a", timeout_seconds=0.05) is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_jetstream_redelivers_after_ack_wait_and_honors_delayed_nak(
    nats_queue: tuple[NatsJetStreamTaskQueue, str, str, str, str],
) -> None:
    """验证超过 ack_wait 会重投，并且延迟 NAK 按预期生效。"""
    queue, _url, _stream, _subject, _consumer = nats_queue
    await queue.publish("task-ack-wait")
    first = await queue.consume("worker-a", timeout_seconds=1)
    assert first is not None

    await asyncio.sleep(0.4)
    redelivered = await queue.consume("worker-b", timeout_seconds=1)
    assert redelivered is not None
    assert redelivered.task_id == first.task_id
    assert redelivered.delivery_sequence > first.delivery_sequence
    assert redelivered.redelivery_count == 1
    await queue.ack(redelivered)

    await queue.publish("task-nak")
    nacked = await queue.consume("worker-a", timeout_seconds=1)
    assert nacked is not None
    await queue.nak(
        nacked,
        delay_seconds=0.2,
        error=DomainFailure("TEMPORARY", "retry", retryable=True),
    )
    assert await queue.consume("worker-a", timeout_seconds=0.05) is None
    await asyncio.sleep(0.2)
    delayed = await queue.consume("worker-a", timeout_seconds=1)
    assert delayed is not None
    assert delayed.task_id == nacked.task_id
    assert delayed.redelivery_count == 1
    await queue.ack(delayed)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_jetstream_provisioning_is_idempotent_and_rejects_incompatible_consumer(
    nats_queue: tuple[NatsJetStreamTaskQueue, str, str, str, str],
) -> None:
    """重复 provision 必须幂等，但不兼容 consumer 配置必须被拒绝。"""
    _queue, url, stream, subject, consumer = nats_queue
    equivalent = await NatsJetStreamTaskQueue.connect(
        url,
        stream,
        subject,
        consumer,
        ack_wait_seconds=0.3,
        max_deliver=3,
    )
    await equivalent.close()

    with pytest.raises(DomainError) as error:
        await NatsJetStreamTaskQueue.connect(
            url,
            stream,
            subject,
            consumer,
            ack_wait_seconds=0.3,
            max_deliver=4,
        )

    assert error.value.failure.code == "QUEUE_CONFIG_MISMATCH"
    assert error.value.failure.retryable is False
